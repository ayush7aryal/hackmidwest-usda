from datetime import UTC, datetime

import pytest

from app import scheduler
from app.db import connect
from test_evaluator import evaluation_sources


def test_scheduler_disabled_by_default(monkeypatch, db_path):
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    assert scheduler.start_scheduler(db_path) is None


def test_three_hour_job_has_no_startup_run_and_no_overlap(monkeypatch, db_path):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("EVALUATION_INTERVAL_HOURS", raising=False)
    monkeypatch.setenv("EVALUATE_ON_STARTUP", "false")
    running = scheduler.start_scheduler(db_path)
    try:
        job = running.get_job("facility-evaluation")
        assert job.max_instances == 1 and job.coalesce is True
        assert job.trigger.interval.total_seconds() == 10800
        assert (job.next_run_time - datetime.now(UTC)).total_seconds() > 10700
    finally:
        running.shutdown()


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_scheduler_rejects_invalid_interval(monkeypatch, db_path, value):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("EVALUATION_INTERVAL_HOURS", value)
    with pytest.raises(ValueError, match="positive whole number"):
        scheduler.start_scheduler(db_path)


def test_scheduled_check_updates_newly_added_facility_database(client, headers, db_path, evaluation_sources):
    created = client.post('/api/locations', headers=headers, json={
        'name': 'New synthetic facility', 'latitude': 30.1766, 'longitude': -85.8055,
        'timezone': 'America/Chicago', 'open_time': '08:00', 'close_time': '18:00'})
    assert created.status_code == 201
    location_id = created.json()['id']
    scheduler.evaluate_all(db_path)
    db = connect(db_path)
    try:
        location = db.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone()
        assert location['status'] == 'TELEWORK' and location['last_checked_at'] is not None
        decision = db.execute('SELECT * FROM decisions WHERE location_id=?', (location_id,)).fetchone()
        assert decision is not None and decision['manager_status'] == 'PENDING'
        assert decision['sent_at'] is None and decision['manager_alert_sent_at'] is None
    finally:
        db.close()


def test_scheduler_checks_each_facility_even_after_failure(monkeypatch, db_path):
    calls = []
    def evaluate(path, location_id):
        calls.append(location_id)
        if location_id == 1:
            raise RuntimeError("Test failure")
        return {"state": "COMPLETED"}
    monkeypatch.setattr(scheduler, "evaluate", evaluate)
    scheduler.evaluate_all(db_path)
    assert calls == [1, 2]
