"""One in-process scheduler; run exactly one Uvicorn worker."""

import logging
import os
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import connect
from app.evaluator import EvaluationBusy, evaluate
from app.thresholds import timestamp

logger = logging.getLogger('uvicorn.error.scheduler')


def evaluate_all(path: str, due_only: bool = False, hours: int = 3) -> None:
    """Reload facilities every run; failures never prevent the next office's check."""
    db = connect(path)
    try:
        now = datetime.now(UTC)
        location_ids = [row['id'] for row in db.execute("SELECT id, last_checked_at FROM locations ORDER BY id")
                        if not due_only or row['last_checked_at'] is None
                        or timestamp(row['last_checked_at']) + timedelta(hours=hours) <= now]
    finally:
        db.close()
    for location_id in location_ids:
        try:
            result = evaluate(path, location_id, scheduled=True)
            if result["state"] != "COMPLETED":
                logger.warning("Facility %s: %s", location_id, result["message"])
            else:
                logger.info("Scheduled facility %s updated; decision %s", location_id, result.get('decision_id'))
        except EvaluationBusy:
            logger.info("Facility %s deferred: another operation is in progress", location_id)
            break  # The next tick reloads saved timestamps and retries overdue facilities.
        except Exception as error:
            logger.error("Facility %s scheduled check failed: %s", location_id, type(error).__name__)


def start_scheduler(path: str) -> BackgroundScheduler | None:
    """Persist cadence in facility timestamps so restarts cannot postpone checks."""
    if os.environ.get("SCHEDULER_ENABLED", "false").lower() != "true":
        return None
    try:
        hours = int(os.environ.get("EVALUATION_INTERVAL_HOURS", "3"))
        if hours < 1:
            raise ValueError
    except ValueError:
        raise ValueError("EVALUATION_INTERVAL_HOURS must be a positive whole number.") from None
    scheduler = BackgroundScheduler(timezone="UTC")
    # Tick every minute, but fetch sources only for facilities due for their 3h check.
    # Missing timestamps (new/edited facilities) and missed runs catch up immediately.
    force_startup = os.environ.get("EVALUATE_ON_STARTUP", "false").lower() == "true"
    scheduler.add_job(evaluate_all, "interval", minutes=1, args=[path, True, hours], id="facility-evaluation",
                      max_instances=1, coalesce=True,
                      next_run_time=datetime.now(UTC) + timedelta(minutes=1 if force_startup else 0))
    if force_startup:
        scheduler.add_job(evaluate_all, args=[path], id="startup-evaluation")
    scheduler.start()
    return scheduler
