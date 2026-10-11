"""Deterministic notification eligibility; the agent never decides delivery."""

import json


def notification_rule(posture: str, override: bool = False) -> dict:
    """A repeated recommendation is not proof that employees were notified."""
    if override:
        return {"required": True, "reason": "manager_override"}
    if posture == "NORMAL":
        return {"required": False, "reason": "normal_operations"}
    return {"required": True, "reason": "disruption"}


def suppress_recipients(rows: list[dict], reason: str) -> list[dict]:
    """Keep exclusions inspectable while unchecking every routine notification."""
    return [row | {"included": False, "reason": reason if row["included"] else row["reason"],
                   "requires_manager_review": False, "warning": None} for row in rows]


def delivery_blocks(db, decision: dict, recipients: list[dict]) -> dict[int, str]:
    """Use actual employee submissions, including unresolved provider outcomes."""
    history = db.execute(
        """SELECT posture, delivery_json FROM decisions WHERE location_id = ? AND id != ?
           AND (sent_at IS NOT NULL OR json_extract(delivery_json, '$.status') IN ('IN_PROGRESS', 'UNKNOWN'))
           AND julianday(affected_start) <= julianday(?) AND julianday(affected_end) >= julianday(?)
           ORDER BY COALESCE(sent_at, json_extract(delivery_json, '$.requested_at')) DESC, id DESC""",
        (decision["location_id"], decision["id"], decision["affected_start"], decision["affected_end"]),
    )
    pending = {row["emp_id"] for row in recipients}
    blocked = {}
    for previous in history:
        delivery = json.loads(previous['delivery_json'] or '{}')
        uncertain = delivery.get('status') in ('IN_PROGRESS', 'UNKNOWN')
        delivered = set(delivery.get('intended_employee_ids' if uncertain else 'accepted_employee_ids', [])) & pending
        if previous["posture"] == decision["posture"]:
            blocked.update({emp_id: 'delivery_uncertain' if uncertain else 'already_notified' for emp_id in delivered})
        pending -= delivered
    return blocked


def notification_recipients(db, decision: dict, recipients: list[dict], override: bool = False) -> list[dict]:
    """Keep unnotified affected employees selected; never bypass prior submissions."""
    rule = notification_rule(decision['posture'], override)
    if not rule['required']:
        return suppress_recipients(recipients, rule['reason'])
    blocked = delivery_blocks(db, decision, recipients)
    return [suppress_recipients([row], blocked[row['emp_id']])[0]
            if row['included'] and row['emp_id'] in blocked else row for row in recipients]


def already_notified(db, decision: dict, recipients: list[dict]) -> set[int]:
    """Final send guard also blocks unresolved submissions for these employees."""
    return set(delivery_blocks(db, decision, recipients))
