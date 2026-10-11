"""Deterministic notification eligibility; the agent never decides delivery."""

import json

from app.thresholds import timestamp


def notification_rule(snapshot: dict, posture: str, start: str, end: str, override: bool = False) -> dict:
    """Suppress routine all-clears and an unchanged action covering the same window."""
    if override:
        return {"required": True, "reason": "manager_override"}
    if posture == "NORMAL":
        return {"required": False, "reason": "normal_operations"}
    if snapshot.get("last_checked_at") and snapshot.get("status") == posture:
        previous_start, previous_end = snapshot.get("affected_start"), snapshot.get("affected_end")
        if previous_start and previous_end and timestamp(previous_start) <= timestamp(start) and timestamp(previous_end) >= timestamp(end):
            return {"required": False, "reason": "unchanged_operation"}
    return {"required": True, "reason": "operation_changed"}


def suppress_recipients(rows: list[dict], reason: str) -> list[dict]:
    """Keep exclusions inspectable while unchecking every routine notification."""
    return [row | {"included": False, "reason": reason if row["included"] else row["reason"],
                   "requires_manager_review": False, "warning": None} for row in rows]


def already_notified(db, decision: dict, recipients: list[dict]) -> set[int]:
    """Compare each recipient's latest submitted action for a covering time window."""
    history = db.execute(
        """SELECT posture, delivery_json FROM decisions WHERE location_id = ? AND id != ?
           AND sent_at IS NOT NULL AND julianday(affected_start) <= julianday(?)
           AND julianday(affected_end) >= julianday(?) ORDER BY sent_at DESC, id DESC""",
        (decision["location_id"], decision["id"], decision["affected_start"], decision["affected_end"]),
    )
    pending = {row["emp_id"] for row in recipients}
    duplicate = set()
    for previous in history:
        delivered = set(json.loads(previous["delivery_json"] or '{}').get("accepted_employee_ids", [])) & pending
        if previous["posture"] == decision["posture"]:
            duplicate.update(delivered)
        pending -= delivered
    return duplicate
