"""Fetch -> policy -> one agent -> validation -> one SQLite evidence snapshot."""

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock

from pydantic import ValidationError

from app import agent, tools
from app.db import connect, utc_now
from app.drafts import prepare_message
from app.models import Recommendation
from app.manager_alerts import notify_manager
from app.notification_rules import notification_recipients, notification_rule
from app.recipients import select_recipients
from app.thresholds import policy_context, timestamp

logger = logging.getLogger(__name__)
operation_lock = Lock()


class EvaluationBusy(RuntimeError):
    """A manual evaluation, scheduled evaluation or facility edit is in progress."""


@contextmanager
def exclusive_operation() -> Iterator[None]:
    """One process-wide lock is sufficient for this two-office, single-worker MVP."""
    if not operation_lock.acquire(blocking=False):
        raise EvaluationBusy("An evaluation or facility edit is in progress; retry shortly.")
    try:
        yield
    finally:
        operation_lock.release()


def material_change(location: dict, result: Recommendation, alerts_changed: bool = False) -> bool:
    """Flag meaningful review changes; repeated all-clear windows need no new alert."""
    if alerts_changed or location["last_checked_at"] is None or location["status"] != result.posture:
        return True
    if result.posture == "NORMAL":
        return False
    for key in ("affected_start", "affected_end"):
        if not location[key] or abs((timestamp(location[key]) - getattr(result, key)).total_seconds()) >= 1800:
            return True
    return False


def weather_alerts_changed(db: sqlite3.Connection, location_id: int, weather: dict) -> bool:
    """New, updated or removed alerts require review even when posture stays NORMAL."""
    if not weather.get("alerts_available", weather["available"]):
        return False  # An outage is not an all-clear or a canceled hazard.
    previous = db.execute("SELECT alternatives_json FROM decisions WHERE location_id = ? ORDER BY id DESC LIMIT 1",
                          (location_id,)).fetchone()
    old_weather = json.loads(previous[0]).get("inputs", {}).get("weather", {}) if previous else {}
    old = old_weather.get("alerts", [])
    # Ignore order; retain all alert content so revised instructions trigger review.
    old_content = sorted(json.dumps(alert, sort_keys=True) for alert in old)
    new_content = sorted(json.dumps(alert, sort_keys=True) for alert in weather.get("alerts", []))
    return old_content != new_content


def evaluate(path: str, location_id: int, manager_id: int | None = None, weekly: bool = False,
             scheduled: bool = False) -> dict:
    """Never mutate last known posture after source/model failure or lost ownership."""
    with exclusive_operation():
        db = connect(path)
        try:
            row = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
            if row is None:
                return {"state": "INSUFFICIENT_DATA", "message": "Facility no longer exists."}
            if manager_id is not None:
                owner = db.execute("SELECT location_id FROM users WHERE emp_id = ? AND is_manager = 1", (manager_id,)).fetchone()
                if owner is None or owner[0] != location_id:
                    return {"state": "INSUFFICIENT_DATA", "message": "Manager facility assignment changed; reload."}
            location = dict(row)
            if weekly:
                from app.weekly import run_weekly_evaluation
                return run_weekly_evaluation(db, location)
            return run_evaluation(db, location, scheduled=scheduled)
        except ValidationError:
            logger.warning("Facility %s: structured recommendation validation failed", location_id)
            return {"state": "INSUFFICIENT_DATA", "message": "Model returned invalid structured advice; retry evaluation."}
        except ValueError as error:
            logger.warning("Facility %s: evaluation rejected (%s)", location_id, type(error).__name__)
            return {"state": "INSUFFICIENT_DATA", "message": str(error)}
        except Exception as error:
            logger.error("Evaluation failed: %s", type(error).__name__)
            return {"state": "INSUFFICIENT_DATA", "message": "Evaluation service failed; check model configuration and source availability, then retry."}
        finally:
            db.close()


def run_evaluation(db: sqlite3.Connection, location: dict, scheduled: bool = False) -> dict:
    """Retain actual compact inputs and original advice for later manager edits."""
    signals = tools.fetch_signals(location["latitude"], location["longitude"])
    context = policy_context(location, signals, datetime.now(UTC))
    result = agent.validate_recommendation(agent.recommend(location, context), context)
    with db:
        decision_id = save_decision(db, location, result, signals, context, update_location=True)
    alert = notify_manager(db, decision_id, scheduled=scheduled)
    message = 'Recommendation saved for manager review. No employee emails were sent.'
    if alert['status'] == 'ACCEPTED':
        message += ' Manager review email accepted by the email provider.'
    elif alert['status'] in ('FAILED', 'UNKNOWN', 'PARTIAL'):
        message += ' Manager email needs attention; see its status in the decision.'
    return {"state": "COMPLETED", "decision_id": decision_id,
            "message": message}


def save_decision(db: sqlite3.Connection, location: dict, result: Recommendation, signals: dict,
                  context: dict, group: dict | None = None, update_location: bool = False,
                  recipients: list[dict] | None = None) -> int:
    """Save an ordinary or date/shift-specific draft using the existing decision table."""
    windows = ([{"start": group["start"], "end": group["end"]}] if group and group.get("shift_id")
               else context.get("impact_windows", []))
    if recipients is None:
        recipients = select_recipients(db, location, result.affected_start.isoformat(),
                                      result.affected_end.isoformat(), result.posture,
                                      shift_id=group.get("shift_id") if group else None, impact_windows=windows)
    notification = notification_rule(result.posture)
    recipients = notification_recipients(db, {
        'id': 0, 'location_id': location['id'], 'posture': result.posture,
        'affected_start': result.affected_start.isoformat(), 'affected_end': result.affected_end.isoformat()}, recipients)
    manager = db.execute("SELECT * FROM users WHERE location_id = ? AND is_manager = 1 ORDER BY emp_id LIMIT 1",
                         (location["id"],)).fetchone()
    draft = result.model_dump(mode="json")
    subject, body = prepare_message(location, draft, dict(manager) if manager else None)
    if group and group.get("shift_name"):
        subject += f" · {group['shift_name']}"
        body += f"\nApplies only to: {group['shift_name']} on {group['date']}.\n"
    changed = material_change(location, result, weather_alerts_changed(db, location["id"], signals["weather"]))
    alternatives = {"uncertainty": result.uncertainty, "what_would_change": result.what_would_change,
                    "warnings": context["warnings"], "policy_version": context["policy_version"],
                    "confidence_note": "Qualitative model estimate; not a calibrated probability.",
                    "material_change": changed, "notification": notification,
                    "manager_alert": "In-app review; employee email requires explicit manager approval and send.",
                    "original_recommendation": result.model_dump(mode="json"),
                    "facility_snapshot": location, "inputs": signals,
                    "selection_windows": windows,
                    "original_recipients": recipients, "original_draft": {"subject": subject, "body": body},
                    "recipient_selection": {"scope": "affected", "include_ids": [], "exclude_ids": []}}
    if group:
        alternatives["message_group"] = group
    now = utc_now()
    cursor = db.execute(
        """INSERT INTO decisions (location_id, evaluated_at, posture, reason, confidence,
           affected_start, affected_end, evidence_json, threshold_json, alternatives_json,
           recipients_json, draft_subject, draft_body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (location["id"], now, result.posture, result.reason, result.confidence,
         result.affected_start.isoformat(), result.affected_end.isoformat(),
         json.dumps(context["evidence"]), json.dumps(context["thresholds"]), json.dumps(alternatives),
         json.dumps(recipients), subject, body),
    )
    if update_location:
        db.execute(
            """UPDATE locations SET status = ?, status_reason = ?, status_confidence = ?,
               affected_start = ?, affected_end = ?, last_checked_at = ?, updated_at = ? WHERE id = ?""",
            (result.posture, result.reason, result.confidence, result.affected_start.isoformat(),
             result.affected_end.isoformat(), now, now, location["id"]),
        )
    return cursor.lastrowid
