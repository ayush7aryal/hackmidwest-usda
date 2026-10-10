from datetime import UTC, datetime

from app import scheduler


def test_scheduler_disabled_by_default(monkeypatch, db_path):
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    assert scheduler.start_scheduler(db_path) is None


def test_hourly_job_has_no_startup_run_and_no_overlap(monkeypatch, db_path):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("EVALUATE_ON_STARTUP", "false")
    running = scheduler.start_scheduler(db_path)
    try:
        job = running.get_job("hourly-facility-evaluation")
        assert job.max_instances == 1 and job.coalesce is True
        assert job.trigger.interval.total_seconds() == 3600
        assert (job.next_run_time - datetime.now(UTC)).total_seconds() > 3500
    finally:
        running.shutdown()


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
