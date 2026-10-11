"""Manager review and fresh recipient previews; delivery lives in email_delivery."""

import json
import sqlite3
from datetime import UTC, datetime

from fastapi import HTTPException

from app.db import utc_now
from app.models import RecipientSelection, ReviewRequest
from app.notification_rules import already_notified, notification_rule, suppress_recipients
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
        rule = notification_rule(alternatives.get("facility_snapshot", {}), posture,
                                 row["affected_start"], row["affected_end"], override)
        rows = select_recipients(db, location, row["affected_start"], row["affected_end"],
                                 "CLOSED" if override and posture == "NORMAL" else posture,
                                 selection.scope, selection.include_ids, selection.exclude_ids,
                                 shift_id=group.get("shift_id") if selection.scope == "affected" else None,
                                 impact_windows=None if override else alternatives.get("selection_windows"))
        if not rule["required"]:
            return suppress_recipients(rows, rule["reason"])
        duplicates = already_notified(db, row | {"posture": posture}, rows)
        return [suppress_recipients([employee], "already_notified")[0] if employee['emp_id'] in duplicates
                and employee['included'] else employee for employee in rows]
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def review_decision(db: sqlite3.Connection, decision_id: int, manager_id: int, body: ReviewRequest) -> None:
    """Save edits and review atomically, preserving original inputs and every prior review."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row, location = owned_decision(db, decision_id, manager_id)
        if row["sent_at"] or json.loads(row["delivery_json"] or '{}').get("status") in ("IN_PROGRESS", "UNKNOWN", "PARTIAL"):
            raise HTTPException(409, "This message has been submitted; evaluate again to prepare another action")
        alternatives = json.loads(row["alternatives_json"])
        recipients = recipient_preview(db, row, location, body, body.posture)
        if body.status == "APPROVED":
            validate_approval(row, location, alternatives, recipients, body)
        reviewed_at = utc_now()
        history = alternatives.setdefault("reviews", [])
        history.append({"reviewed_by": manager_id, "reviewed_at": reviewed_at,
                        "status": body.status, "posture": body.posture, "reason": body.reason,
                        "draft_subject": body.draft_subject, "draft_body": body.draft_body,
                        "selection": body.model_dump(include={"scope", "include_ids", "exclude_ids"}),
                        "essential_confirmed": body.essential_confirmed})
        alternatives["recipient_selection"] = body.model_dump(include={"scope", "include_ids", "exclude_ids"})
        alternatives["notification"] = notification_rule(
            alternatives.get("facility_snapshot", {}), body.posture, row["affected_start"], row["affected_end"],
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
    if timestamp(row["affected_end"]) <= datetime.now(UTC):
        raise HTTPException(409, "Impact window has expired; evaluate again before approving")
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
