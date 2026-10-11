"""Manager review and fresh recipient previews; delivery lives in email_delivery."""

import json
import sqlite3
from datetime import UTC, datetime

from fastapi import HTTPException

from app.db import utc_now
from app.models import RecipientSelection, ReviewRequest
from app.notification_rules import delivery_blocks, notification_recipients, notification_rule
from app.recipients import select_recipients
from app.thresholds import timestamp


def owned_decision(db: sqlite3.Connection, decision_id: int, manager_id: int) -> tuple[dict, dict]:
    """Reload ownership on every review/preview, including after facility reassignment."""
    row = db.execute(
        """SELECT d.* FROM decisions d JOIN users u ON u.location_id = d.location_id
           WHERE d.id = ? AND u.emp_id = ? AND u.is_manager = 1""", (decision_id, manager_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Decision not found")
    location = db.execute("SELECT * FROM locations WHERE id = ?", (row["location_id"],)).fetchone()
    return dict(row), dict(location)


def recipient_preview(db: sqlite3.Connection, row: dict, location: dict, selection: RecipientSelection, posture: str) -> list[dict]:
    """Recompute from current staff/shifts; client-selected IDs never bypass ownership."""
    if row["affected_start"] is None or row["affected_end"] is None:
        raise HTTPException(409, "Decision has no validated impact window; evaluate again")
    try:
        alternatives = json.loads(row["alternatives_json"])
        group = alternatives.get("message_group", {})
        original = alternatives.get("original_recommendation", {}).get("posture", row["posture"])
        override = posture != original
        rows = select_recipients(db, location, row["affected_start"], row["affected_end"],
                                 "CLOSED" if override and posture == "NORMAL" else posture,
                                 selection.scope, selection.include_ids, selection.exclude_ids,
                                 shift_id=group.get("shift_id") if selection.scope == "affected" else None,
                                 impact_windows=None if override else alternatives.get("selection_windows"))
        return notification_recipients(db, row | {'posture': posture}, rows, override,
                                       selection.include_ids, selection.scope == 'all_staff')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def review_decision(db: sqlite3.Connection, decision_id: int, manager_id: int, body: ReviewRequest) -> None:
    """Save edits and review atomically, preserving original inputs and every prior review."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row, location = owned_decision(db, decision_id, manager_id)
        delivery = json.loads(row["delivery_json"] or '{}')
        if delivery.get('status') in ('IN_PROGRESS', 'UNKNOWN'):
            raise HTTPException(409, "Check the pending or uncertain provider submission before sending again")
        submitted = bool(row['sent_at'] or delivery.get('status') in ('ACCEPTED', 'PARTIAL'))
        alternatives = json.loads(row["alternatives_json"])
        recipients = recipient_preview(db, row, location, body, body.posture)
        if body.status == "APPROVED":
            validate_approval(row, location, alternatives, recipients, body)
            selected = [employee for employee in recipients if employee['included']]
            blocked = delivery_blocks(db, row | {'posture': body.posture}, selected)
            if 'delivery_uncertain' in blocked.values():
                raise HTTPException(409, 'Check the unresolved provider submission before sending to these employees again')
            if (submitted or alternatives.get('delivery_history') or blocked) and not body.resend_confirmed:
                raise HTTPException(409, "Confirm sending again to previously notified employees")
        elif submitted:
            raise HTTPException(409, "Previously sent messages can be edited and approved for a confirmed resend")
        if submitted:
            alternatives.setdefault('delivery_history', []).append({
                'posture': row['posture'], 'sent_at': row['sent_at'], 'delivery': delivery,
                'draft_subject': row['draft_subject'], 'draft_body': row['draft_body'],
                'recipients': json.loads(row['recipients_json'])})
            db.execute('UPDATE decisions SET sent_at = NULL, delivery_json = NULL WHERE id = ?', (decision_id,))
        reviewed_at = utc_now()
        history = alternatives.setdefault("reviews", [])
        history.append({"reviewed_by": manager_id, "reviewed_at": reviewed_at,
                        "status": body.status, "posture": body.posture, "reason": body.reason,
                        "draft_subject": body.draft_subject, "draft_body": body.draft_body,
                        "selection": body.model_dump(include={"scope", "include_ids", "exclude_ids"}),
                        "essential_confirmed": body.essential_confirmed,
                        "resend_confirmed": body.resend_confirmed,
                        "historical_demo_confirmed": body.historical_demo_confirmed})
        alternatives["recipient_selection"] = body.model_dump(include={"scope", "include_ids", "exclude_ids"})
        alternatives["notification"] = notification_rule(
            body.posture,
            body.scope == "all_staff" or bool(body.include_ids) or
            body.posture != alternatives.get("original_recommendation", {}).get("posture", row["posture"]))
        message = labeled_message(body.draft_body, body.status, manager_id)
        db.execute(
            """UPDATE decisions SET posture = ?, reason = ?, draft_subject = ?, draft_body = ?,
               manager_status = ?, reviewed_by = ?, reviewed_at = ?, recipients_json = ?,
               alternatives_json = ? WHERE id = ?""",
            (body.posture, body.reason, body.draft_subject, message, body.status, manager_id,
             reviewed_at, json.dumps(recipients), json.dumps(alternatives), decision_id),
        )


def validate_approval(row: dict, location: dict, alternatives: dict, recipients: list[dict], body: ReviewRequest) -> None:
    """Expired/configuration-stale advice and essential personnel require explicit handling."""
    if timestamp(row["affected_end"]) <= datetime.now(UTC) and not body.historical_demo_confirmed:
        raise HTTPException(409, "Impact window has expired; refresh the assessment or confirm a historical demo email")
    snapshot = alternatives.get("facility_snapshot", {})
    keys = ("name", "latitude", "longitude", "timezone", "open_time", "close_time")
    if snapshot and any(snapshot[key] != location[key] for key in keys):
        raise HTTPException(409, "Facility configuration changed since evaluation; evaluate again")
    if any(row["requires_manager_review"] for row in recipients) and not body.essential_confirmed:
        raise HTTPException(422, "Confirm specific instructions for included essential personnel before approving")


def labeled_message(body: str, status: str, manager_id: int) -> str:
    """Keep review metadata out of approved employee messages and exports."""
    lines = body.splitlines()
    if lines and lines[0].startswith(("DRAFT —", "APPROVED —", "REJECTED —")):
        body = "\n".join(lines[1:]).lstrip()
    if status == "APPROVED":
        # Also clean messages generated before draft status was separated from rationale.
        return body.replace(" This is a draft; your manager decides the final action.", "")
    label = "DRAFT — pending manager approval; this is not a finalized instruction." if status == "PENDING" else f"{status} — reviewed by manager {manager_id}."
    return f"{label}\n\n{body}"
