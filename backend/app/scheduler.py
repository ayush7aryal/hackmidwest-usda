"""One in-process scheduler; run exactly one Uvicorn worker."""

import logging
import os
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import connect
from app.evaluator import EvaluationBusy, evaluate

logger = logging.getLogger(__name__)


def evaluate_all(path: str) -> None:
    """Reload facilities every run; failures never prevent the next office's check."""
    db = connect(path)
    try:
        location_ids = [row[0] for row in db.execute("SELECT id FROM locations ORDER BY id")]
    finally:
        db.close()
    for location_id in location_ids:
        try:
            result = evaluate(path, location_id)
            if result["state"] != "COMPLETED":
                logger.warning("Facility %s: %s", location_id, result["message"])
        except EvaluationBusy:
            logger.info("Facility %s skipped: another operation is in progress", location_id)
        except Exception as error:
            logger.error("Facility %s scheduled check failed: %s", location_id, type(error).__name__)


def start_scheduler(path: str) -> BackgroundScheduler | None:
    """No surprise startup evaluation unless explicitly enabled in configuration."""
    if os.environ.get("SCHEDULER_ENABLED", "false").lower() != "true":
        return None
    try:
        hours = int(os.environ.get("EVALUATION_INTERVAL_HOURS", "3"))
        if hours < 1:
            raise ValueError
    except ValueError:
        raise ValueError("EVALUATION_INTERVAL_HOURS must be a positive whole number.") from None
    scheduler = BackgroundScheduler(timezone="UTC")
    options = {"next_run_time": datetime.now(UTC)} if os.environ.get("EVALUATE_ON_STARTUP", "false").lower() == "true" else {}
    scheduler.add_job(evaluate_all, "interval", hours=hours, args=[path], id="facility-evaluation",
                      max_instances=1, coalesce=True, **options)
    scheduler.start()
    return scheduler
