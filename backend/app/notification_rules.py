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
        """SELECT posture, delivery_json, alternatives_json FROM decisions WHERE location_id = ? AND id != ?
           AND (sent_at IS NOT NULL OR json_extract(delivery_json, '$.status') IN ('IN_PROGRESS', 'UNKNOWN')
                OR json_array_length(alternatives_json, '$.delivery_history') > 0)
           AND julianday(affected_start) <= julianday(?) AND julianday(affected_end) >= julianday(?)
           ORDER BY COALESCE(sent_at, json_extract(delivery_json, '$.requested_at')) DESC, id DESC""",
        (decision["location_id"], decision["id"], decision["affected_start"], decision["affected_end"]),
    )
    submissions = []
    for previous in history:
        archived = json.loads(previous['alternatives_json']).get('delivery_history', [])
        submissions.extend((item['posture'], item['delivery']) for item in archived)
        submissions.append((previous['posture'], json.loads(previous['delivery_json'] or '{}')))
    submissions.sort(key=lambda item: item[1].get('requested_at', ''), reverse=True)
    pending = {row["emp_id"] for row in recipients}
    blocked = {}
    for posture, delivery in submissions:
        uncertain = delivery.get('status') in ('IN_PROGRESS', 'UNKNOWN')
        delivered = set(delivery.get('intended_employee_ids' if uncertain else 'accepted_employee_ids', [])) & pending
        if posture == decision["posture"]:
            blocked.update({emp_id: 'delivery_uncertain' if uncertain else 'already_notified' for emp_id in delivered})
        pending -= delivered
    return blocked


def notification_recipients(db, decision: dict, recipients: list[dict], override: bool = False,
                            manual_ids: list[int] | None = None, all_staff: bool = False) -> list[dict]:
    """Suggest an audience while allowing explicit manager choices, including normal notices."""
    rule = notification_rule(decision['posture'], override)
    blocked = delivery_blocks(db, decision, recipients)
    result = []
    for row in recipients:
        manual = all_staff or row['emp_id'] in (manual_ids or [])
        if row['included'] and manual and row['emp_id'] in blocked:
            row = row | {'previous_delivery': blocked[row['emp_id']]}
        elif not manual and not rule['required']:
            row = suppress_recipients([row], rule['reason'])[0]
        elif row['included'] and not manual and row['emp_id'] in blocked:
            row = suppress_recipients([row], blocked[row['emp_id']])[0]
        result.append(row)
    return result
