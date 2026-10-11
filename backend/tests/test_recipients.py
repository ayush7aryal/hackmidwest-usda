from contextlib import closing
from datetime import UTC, datetime

import pytest

from app.db import connect
from app.recipients import scheduled_overlap, select_recipients

WINDOW = ("2026-10-12T08:00:00-05:00", "2026-10-12T11:00:00-05:00")


def selection(db_path, **kwargs):
    with closing(connect(db_path)) as db:
        office = dict(db.execute("SELECT * FROM locations WHERE id = 1").fetchone())
        return select_recipients(db, office, *WINDOW, "TELEWORK", **kwargs)


def test_actual_shift_date_flags_and_every_exclusion(db_path):
    rows = {row["emp_id"]: row for row in selection(db_path)}
    assert len(rows) == 7
    assert rows[1001]["reason"] == "manager"
    assert rows[1101]["included"] is True
    assert rows[1102]["included"] is True and rows[1102]["requires_manager_review"] is True
    assert rows[1103]["reason"] == "not_scheduled"
    assert rows[1106]["reason"] == "not_scheduled"
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET shift_id = 1 WHERE emp_id = 1103")
    assert next(row for row in selection(db_path) if row["emp_id"] == 1103)["reason"] == "already_remote"


def test_half_open_boundary_and_operating_hours(db_path):
    with closing(connect(db_path)) as db:
        office = dict(db.execute("SELECT * FROM locations WHERE id = 1").fetchone())
        rows = select_recipients(db, office, "2026-10-12T16:00:00-05:00", "2026-10-12T17:00:00-05:00", "EARLY_DISMISSAL")
        assert next(row for row in rows if row["emp_id"] == 1101)["reason"] == "not_scheduled"
        rows = select_recipients(db, office, "2026-10-12T18:00:00-05:00", "2026-10-12T19:00:00-05:00", "CLOSED")
        assert not any(row["included"] for row in rows)


def test_midnight_window_matches_the_correct_next_day(db_path):
    with closing(connect(db_path)) as db:
        office = dict(db.execute("SELECT * FROM locations WHERE id = 1").fetchone())
        rows = select_recipients(db, office, "2026-10-11T23:00:00-05:00", "2026-10-12T09:00:00-05:00", "CLOSED")
        assert next(row for row in rows if row["emp_id"] == 1101)["included"] is True
        assert next(row for row in rows if row["emp_id"] == 1103)["included"] is False


def test_facility_wide_explicitly_includes_remote_unscheduled_staff(db_path):
    rows = selection(db_path, scope="all_staff")
    assert len([row for row in rows if row["included"]]) == 7
    assert all(row["reason"] == "facility_wide" for row in rows if not row["is_manager"])
    assert len([row for row in selection(db_path, scope="all_staff", include_ids=[1001]) if row["included"]]) == 7


def test_manager_overrides_and_ownership_are_validated(db_path):
    assert next(row for row in selection(db_path, exclude_ids=[1101]) if row["emp_id"] == 1101)["reason"] == "manager_excluded"
    for options in ({"include_ids": [2101]}, {"exclude_ids": [2101]},
                    {"include_ids": [1101], "exclude_ids": [1101]}):
        with pytest.raises(ValueError):
            selection(db_path, **options)


def test_dst_offsets_and_nonexistent_shift_boundaries():
    location = {"timezone": "America/Chicago", "open_time": "00:00", "close_time": "06:00"}
    hours = {"sun": ["01:00", "04:00"]}
    assert scheduled_overlap(location, hours, datetime(2026, 11, 1, 7, 30, tzinfo=UTC), datetime(2026, 11, 1, 8, tzinfo=UTC))
    with pytest.raises(ValueError, match="daylight-saving gap"):
        scheduled_overlap(location, {"sun": ["02:30", "04:00"]},
                          datetime(2026, 3, 8, 7, tzinfo=UTC), datetime(2026, 3, 8, 10, tzinfo=UTC))


def test_invalid_shift_is_explained_and_long_windows_are_rejected(db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE shifts SET weekly_hours_json = 'bad JSON' WHERE id = 1")
    assert next(row for row in selection(db_path) if row["emp_id"] == 1101)["reason"] == "invalid_shift"
    with closing(connect(db_path)) as db:
        office = dict(db.execute("SELECT * FROM locations WHERE id = 1").fetchone())
        with pytest.raises(ValueError, match="three days"):
            select_recipients(db, office, "2026-10-01T00:00:00Z", "2026-10-10T00:00:00Z", "CLOSED")


def test_separate_windows_exclude_staff_working_only_in_the_gap(db_path):
    import json
    windows = [{'start': '2026-10-14T08:00:00-05:00', 'end': '2026-10-14T09:00:00-05:00'},
               {'start': '2026-10-14T17:00:00-05:00', 'end': '2026-10-14T18:00:00-05:00'}]
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE shifts SET weekly_hours_json=? WHERE id=3', (json.dumps({'wed': ['10:00', '16:00']}),))
        db.execute('UPDATE users SET is_teleworking=0 WHERE emp_id=1103')
        office = dict(db.execute('SELECT * FROM locations WHERE id=1').fetchone())
        rows = select_recipients(db, office, '2026-10-14T08:00:00-05:00', '2026-10-14T18:00:00-05:00',
                                 'TELEWORK', impact_windows=windows)
        assert {row['emp_id'] for row in rows if row['included']} == {1101, 1102, 1104, 1105}
        assert next(row for row in rows if row['emp_id'] == 1103)['reason'] == 'not_scheduled'


def test_roster_decodes_each_shift_once_and_does_not_leak_internal_fields(db_path, monkeypatch):
    from app import recipients
    calls, statements = [], []
    original = recipients.json.loads
    def decode(value):
        calls.append(value)
        return original(value)
    with closing(connect(db_path)) as db:
        office = dict(db.execute('SELECT * FROM locations WHERE id=1').fetchone())
        db.set_trace_callback(statements.append)
        monkeypatch.setattr(recipients.json, 'loads', decode)
        roster = recipients.load_roster(db, 1)
        for date in ['2026-10-12', '2026-10-14']:
            rows = recipients.filter_roster(roster, office, date+'T08:00:00-05:00', date+'T11:00:00-05:00', 'TELEWORK')
            assert {row['emp_id'] for row in rows if row['included']} == {1101, 1102, 1104, 1105}
            assert all('_invalid_shift' not in row for row in rows)
        assert len(calls) == 3
        assert len(statements) == 1
        plan = db.execute('EXPLAIN QUERY PLAN SELECT u.* FROM users u LEFT JOIN shifts s ON s.id=u.shift_id WHERE u.location_id=?', (1,)).fetchall()
        assert any('idx_users_location' in row['detail'] for row in plan)


def test_equal_instants_with_different_offsets_produce_identical_recipients(db_path):
    with closing(connect(db_path)) as db:
        office = dict(db.execute('SELECT * FROM locations WHERE id=1').fetchone())
        local = select_recipients(db, office, '2026-10-14T16:00:00-05:00', '2026-10-14T18:00:00-05:00', 'CLOSED')
        utc = select_recipients(db, office, '2026-10-14T21:00:00Z', '2026-10-14T23:00:00Z', 'CLOSED')
        assert local == utc
        assert {row['emp_id'] for row in utc if row['included']} == {1102, 1105}


@pytest.mark.parametrize('windows', [
    [{'start': '2026-10-14T08:00:00', 'end': '2026-10-14T10:00:00-05:00'}],
    [{'start': '2026-10-14T10:00:00-05:00', 'end': '2026-10-14T08:00:00-05:00'}],
    [{'start': '2026-10-14T08:00:00-05:00'}],
])
def test_invalid_impact_intervals_never_produce_recipients(db_path, windows):
    with pytest.raises(ValueError):
        selection(db_path, impact_windows=windows)


@pytest.mark.parametrize('raw', ['[]', 'true', '42', '{"mon": [8, 16]}'])
def test_invalid_weekday_shapes_are_excluded(db_path, raw):
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE shifts SET weekly_hours_json=? WHERE id=1', (raw,))
    rows = selection(db_path)
    assert next(row for row in rows if row['emp_id'] == 1101)['reason'] == 'invalid_shift'


def test_interval_normalization_merges_duplicates_without_filling_gaps():
    from app.recipients import selection_intervals
    windows = [{'start': '2026-10-14T13:00:00Z', 'end': '2026-10-14T14:00:00Z'},
               {'start': '2026-10-14T08:00:00-05:00', 'end': '2026-10-14T09:00:00-05:00'},
               {'start': '2026-10-14T22:00:00Z', 'end': '2026-10-14T23:00:00Z'}]
    intervals = selection_intervals('2026-10-14T13:00:00Z', '2026-10-14T23:00:00Z', windows)
    assert len(intervals) == 2
    assert (intervals[1][0] - intervals[0][1]).total_seconds() == 8*3600


def test_manual_selection_includes_remote_unscheduled_and_invalid_schedule_staff(db_path):
    rows=selection(db_path,include_ids=[1103,1106])
    assert all(row['included'] and row['reason']=='manager_selected' for row in rows if row['emp_id'] in [1103,1106])
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE shifts SET weekly_hours_json='bad JSON' WHERE id=1")
    row=next(row for row in selection(db_path,include_ids=[1101]) if row['emp_id']==1101)
    assert row['included'] and row['weekly_hours'] is None
