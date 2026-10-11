from contextlib import closing

import pytest

from app.db import connect
from app.recipients import select_recipients
from app import scheduler
from demo_data import expand_demo, FACILITIES


def test_expansion_preserves_edits_and_is_repeatable(db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE locations SET name='Existing edited facility' WHERE id=1")
    assert expand_demo(db_path, 'owner@gmail.com') == {'locations': 5, 'shifts': 6, 'users': 85}
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE locations SET name='Manager edited demo facility' WHERE id=51")
        db.execute("UPDATE users SET name='Manager edited employee' WHERE emp_id=51101")
    assert expand_demo(db_path, 'owner@gmail.com') == {'locations': 0, 'shifts': 0, 'users': 0}
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT name FROM locations WHERE id=1').fetchone()[0] == 'Existing edited facility'
        assert db.execute('SELECT name FROM locations WHERE id=51').fetchone()[0] == 'Manager edited demo facility'
        assert db.execute('SELECT name FROM users WHERE emp_id=51101').fetchone()[0] == 'Manager edited employee'
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'users','locations','shifts','decisions'}
        employees = db.execute('SELECT email FROM users WHERE is_manager=0 ORDER BY emp_id').fetchall()
        assert [r[0] for r in employees] == [f'owner+{i}@gmail.com' for i in range(1, 93)]
        assert db.execute('SELECT email FROM users WHERE emp_id=1001').fetchone()[0] == 'owner@gmail.com'
        assert db.execute('SELECT email FROM users WHERE emp_id=51001').fetchone()[0] == 'owner+manager51001@gmail.com'


def test_five_new_manager_logins_have_independent_rosters(client, db_path):
    expand_demo(db_path)
    for location_id, _, _, _, _, manager_id, _ in FACILITIES:
        login = client.post('/api/login', json={'emp_id': manager_id})
        assert login.status_code == 200
        headers = {'Authorization': 'Bearer '+login.json()['access_token']}
        assert [r['id'] for r in client.get('/api/locations', headers=headers).json()] == [location_id]
        employees = client.get(f'/api/locations/{location_id}/employees', headers=headers).json()
        assert len(employees) == 17 and sum(r['is_manager'] for r in employees) == 1
        assert client.get('/api/locations/1', headers=headers).status_code == 404


@pytest.mark.parametrize('start,end,expected_shift', [
    ('2026-12-25T06:00:00Z','2026-12-25T12:00:00Z',51),
    ('2026-12-25T12:00:00Z','2026-12-25T18:00:00Z',52),
    ('2026-12-25T18:00:00Z','2026-12-26T00:00:00Z',53),
    ('2026-12-26T00:00:00Z','2026-12-26T05:59:00Z',54),
    ('2026-12-26T15:00:00Z','2026-12-26T23:00:00Z',56),
])
def test_day_evening_weekend_and_christmas_coverage(db_path, start, end, expected_shift):
    expand_demo(db_path)
    with closing(connect(db_path)) as db:
        location = dict(db.execute('SELECT * FROM locations WHERE id=51').fetchone())
        rows = select_recipients(db, location, start, end, 'CLOSED')
        included = [r for r in rows if r['included']]
        assert any(r['shift_id'] == expected_shift for r in included)
        assert all(not r['is_manager'] and not r['is_teleworking'] and r['location_id']==51 for r in included)
        assert any(r['requires_manager_review'] for r in included)


def test_id_collision_rolls_back_entire_expansion(db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("INSERT INTO locations SELECT 51,'Unrelated facility',latitude,longitude,timezone,open_time,close_time,status,status_reason,status_confidence,affected_start,affected_end,last_checked_at,updated_at FROM locations WHERE id=1")
    with pytest.raises(ValueError, match='collision'):
        expand_demo(db_path)
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 14


def test_scheduler_includes_every_added_demo_facility(db_path, monkeypatch):
    expand_demo(db_path)
    calls=[]
    monkeypatch.setattr(scheduler, 'evaluate', lambda path, loc: calls.append(loc) or {'state':'COMPLETED'})
    scheduler.evaluate_all(db_path)
    assert calls == [1,2,51,52,53,54,55]
