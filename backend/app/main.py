"""Manager API for facility configuration, live evaluation, filtering and review."""

import hmac
import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app import models
from app import evaluator, review, tools
from app.auth import issue_token, read_token
from app.db import connect, database_path, initialize, utc_now
from app.scheduler import start_scheduler

bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api")
RouteID = Annotated[int, Path(gt=0, le=9_007_199_254_740_991)]


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    """Close request connections even when validation or authorization fails."""
    connection = connect(request.app.state.database_path)
    try:
        yield connection
    finally:
        connection.close()


DB = Annotated[sqlite3.Connection, Depends(get_db)]


def current_manager(
    request: Request,
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> sqlite3.Row:
    """Reload the user so sessions follow facility edits and removed manager roles."""
    if credentials is None:
        raise HTTPException(401, "Manager login required")
    emp_id = read_token(credentials.credentials, request.app.state.session_secret)
    manager = db.execute("SELECT * FROM users WHERE emp_id = ? AND is_manager = 1", (emp_id,)).fetchone()
    if manager is None:
        raise HTTPException(401, "Manager session is no longer valid")
    return manager


ManagerSession = Annotated[sqlite3.Row, Depends(current_manager)]


def mutation_guard() -> Iterator[None]:
    """Serialize facility edits with evaluations so coordinates cannot change mid-check."""
    try:
        with evaluator.exclusive_operation():
            yield
    except evaluator.EvaluationBusy as error:
        raise HTTPException(409, str(error)) from None


def owned_location(db: sqlite3.Connection, manager: sqlite3.Row, location_id: int) -> sqlite3.Row:
    """Return 404 for both missing and inaccessible facilities to avoid disclosure."""
    if location_id != manager["location_id"]:
        raise HTTPException(404, "Facility not found")
    location = db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if location is None:
        raise HTTPException(404, "Facility not found")
    return location


def create_app(path: str | None = None) -> FastAPI:
    """Allow isolated test databases and enforce password configuration outside demo mode."""
    demo_login = os.environ.get("DEMO_LOGIN", "true").lower() == "true"
    password = os.environ.get("MANAGER_PASSWORD", "")
    secret = os.environ.get("SESSION_SECRET", "")
    if not demo_login and (len(password) < 12 or len(secret) < 32):
        raise RuntimeError("Public login requires MANAGER_PASSWORD (12+ characters) and SESSION_SECRET (32+ characters)")
    if secret and len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must have at least 32 characters")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Initialize persistence and the optional single-worker hourly scheduler."""
        initialize(application.state.database_path)
        scheduler = start_scheduler(application.state.database_path)
        application.state.scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    application = FastAPI(title="Facility Operations AI", version="0.1.0", lifespan=lifespan)
    application.state.database_path = path or database_path()
    application.state.session_secret = secret or secrets.token_hex(32)
    application.state.demo_login = demo_login
    application.state.manager_password = password
    application.include_router(router)
    return application


@router.get("/health", response_model=models.Health)
def health(db: DB) -> dict:
    """Confirm that the database is readable, without revealing staff or configuration."""
    db.execute("SELECT id FROM locations LIMIT 1").fetchone()
    return {"status": "ok"}


@router.post("/login", response_model=models.LoginResponse)
def login(body: models.LoginRequest, request: Request, db: DB) -> dict:
    """Use a local demo selector or the configured shared manager demo password."""
    configured = request.app.state.manager_password
    if (configured and not hmac.compare_digest((body.password or "").encode(), configured.encode())):
        raise HTTPException(401, "Invalid manager credentials")
    manager = db.execute("SELECT * FROM users WHERE emp_id = ? AND is_manager = 1", (body.emp_id,)).fetchone()
    if manager is None:
        raise HTTPException(401, "Invalid manager credentials")
    return {"access_token": issue_token(body.emp_id, request.app.state.session_secret), "manager": dict(manager)}


@router.get("/locations", response_model=list[models.Location])
def locations(db: DB, manager: ManagerSession) -> list[dict]:
    """A manager sees exactly the facility assigned in their current user record."""
    return [dict(owned_location(db, manager, manager["location_id"]))]


@router.get("/locations/{location_id}", response_model=models.Location)
def location(location_id: RouteID, db: DB, manager: ManagerSession) -> dict:
    """Read the latest facility state without manufacturing an evaluation."""
    return dict(owned_location(db, manager, location_id))


@router.post("/locations", response_model=models.Location, status_code=201, dependencies=[Depends(mutation_guard)])
def create_location(body: models.LocationCreate, db: DB, manager: ManagerSession) -> dict:
    """Reuse the manager's existing synthetic employee sheet at the new demo location."""
    try:
        with db:
            cursor = db.execute(
                """INSERT INTO locations
                   (id, name, latitude, longitude, timezone, open_time, close_time, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (body.id, body.name, body.latitude, body.longitude, body.timezone,
                 body.open_time, body.close_time, utc_now()),
            )
            location_id = cursor.lastrowid
            db.execute(
                """UPDATE users SET location_id = ? WHERE location_id = ?
                   AND (is_manager = 0 OR emp_id = ?)""",
                (location_id, manager["location_id"], manager["emp_id"]),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Facility ID is already in use") from None
    return dict(db.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone())


@router.patch("/locations/{location_id}", response_model=models.Location, dependencies=[Depends(mutation_guard)])
def update_location(location_id: RouteID, body: models.LocationUpdate, db: DB, manager: ManagerSession) -> dict:
    """Edit facility identity/coordinates atomically, preserving every foreign key."""
    existing = owned_location(db, manager, location_id)
    values = {key: existing[key] for key in models.LocationFields.model_fields}
    changes = body.model_dump(exclude_unset=True)
    new_id = changes.pop("id", location_id)
    try:
        merged = models.LocationFields(**(values | changes))
    except ValidationError as error:
        raise HTTPException(422, error.errors(include_context=False)) from None
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            # The blueprint's FKs have no cascade: defer checks until all references move.
            db.execute("PRAGMA defer_foreign_keys = ON")
            db.execute(
                """UPDATE locations SET id = ?, name = ?, latitude = ?, longitude = ?,
                   timezone = ?, open_time = ?, close_time = ?, updated_at = ? WHERE id = ?""",
                (new_id, merged.name, merged.latitude, merged.longitude, merged.timezone,
                 merged.open_time, merged.close_time, utc_now(), location_id),
            )
            if new_id != location_id:
                db.execute("UPDATE users SET location_id = ? WHERE location_id = ?", (new_id, location_id))
                db.execute("UPDATE decisions SET location_id = ? WHERE location_id = ?", (new_id, location_id))
            if any(existing[key] != value for key, value in merged.model_dump().items()):
                db.execute("""UPDATE locations SET last_checked_at = NULL, status_confidence = NULL,
                           affected_start = NULL, affected_end = NULL,
                           status_reason = 'Facility configuration changed; evaluate again.' WHERE id = ?""", (new_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Facility ID is already in use") from None
    return dict(db.execute("SELECT * FROM locations WHERE id = ?", (new_id,)).fetchone())


@router.get("/locations/{location_id}/employees", response_model=list[models.Employee])
def employees(location_id: RouteID, db: DB, manager: ManagerSession) -> list[dict]:
    """Expose staff/shift assignments and flags for verification; filtering is Phase 4."""
    owned_location(db, manager, location_id)
    rows = db.execute(
        """SELECT u.*, s.name AS shift_name, s.weekly_hours_json FROM users u
           LEFT JOIN shifts s ON s.id = u.shift_id WHERE u.location_id = ? ORDER BY u.emp_id""",
        (location_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        hours = item.pop("weekly_hours_json")
        item["weekly_hours"] = json.loads(hours) if hours else None
        result.append(item)
    return result


@router.get("/locations/{location_id}/weather-alerts", response_model=models.WeatherAlertSnapshot)
def weather_alerts(location_id: RouteID, db: DB, manager: ManagerSession) -> dict:
    """Show every current NWS alert even when forecasting or the adviser is unavailable."""
    location = owned_location(db, manager, location_id)
    snapshot = tools.get_nws_weather(location["latitude"], location["longitude"], include_forecast=False)
    return {"available": snapshot["alerts_available"], "fetched_at": snapshot["fetched_at"],
            "source_url": snapshot["source_urls"][0], "alerts": snapshot["alerts"],
            "error": snapshot.get("alerts_error") or snapshot.get("error")}


@router.get("/locations/{location_id}/decisions", response_model=list[models.DecisionSummary])
def decisions(
    location_id: RouteID, db: DB, manager: ManagerSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    """Bound history reads and keep newest completed evaluations first."""
    owned_location(db, manager, location_id)
    return [dict(row) for row in db.execute(
        "SELECT * FROM decisions WHERE location_id = ? ORDER BY evaluated_at DESC, id DESC LIMIT ? OFFSET ?",
        (location_id, limit, offset),
    ).fetchall()]


@router.get("/decisions/{decision_id}", response_model=models.Decision)
def decision(decision_id: RouteID, db: DB, manager: ManagerSession) -> dict:
    """Decode frozen JSON snapshots only after checking facility access."""
    row = db.execute("SELECT * FROM decisions WHERE id = ? AND location_id = ?",
                     (decision_id, manager["location_id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Decision not found")
    result = dict(row)
    if result["manager_status"] == "APPROVED":
        # Existing approvals also export clean employee text; audit snapshots stay intact.
        result["draft_body"] = review.labeled_message(result["draft_body"], "APPROVED", result["reviewed_by"])
    for stored, exposed in (("evidence_json", "evidence"), ("threshold_json", "thresholds"),
                            ("alternatives_json", "alternatives"), ("recipients_json", "recipients"),
                            ("delivery_json", "delivery")):
        raw = result.pop(stored)
        result[exposed] = json.loads(raw) if raw is not None else None
    return result


@router.post("/locations/{location_id}/evaluate", response_model=models.EvaluationResult)
def evaluate_location(location_id: RouteID, request: Request, db: DB, manager: ManagerSession) -> dict:
    """Run real sources and the adviser; a manager must still review the saved output."""
    owned_location(db, manager, location_id)
    try:
        return evaluator.evaluate(request.app.state.database_path, location_id, manager["emp_id"])
    except evaluator.EvaluationBusy as error:
        raise HTTPException(409, str(error)) from None


@router.post("/decisions/{decision_id}/recipients", response_model=list[models.EmployeeRecipient])
def preview_recipients(decision_id: RouteID, body: models.RecipientSelection, db: DB, manager: ManagerSession) -> list[dict]:
    """Preview current inclusion/exclusion reasons without sending or changing the snapshot."""
    row, facility = review.owned_decision(db, decision_id, manager["emp_id"])
    return review.recipient_preview(db, row, facility, body, body.posture or row["posture"])


@router.post("/locations/{location_id}/evaluate-week", response_model=models.WeeklyEvaluationResult)
def evaluate_week(location_id: RouteID, request: Request, db: DB, manager: ManagerSession) -> dict:
    """Check seven facility-local dates and prepare independent affected-shift drafts."""
    owned_location(db, manager, location_id)
    try:
        return evaluator.evaluate(request.app.state.database_path, location_id, manager["emp_id"], weekly=True)
    except evaluator.EvaluationBusy as error:
        raise HTTPException(409, str(error)) from None


@router.get("/locations/{location_id}/week-plan", response_model=models.WeeklyEvaluationResult | None)
def week_plan(location_id: RouteID, db: DB, manager: ManagerSession) -> dict | None:
    """Restore the latest weekly review and current approval states after navigation."""
    location = owned_location(db, manager, location_id)
    row = db.execute("""SELECT alternatives_json FROM decisions WHERE location_id = ?
                     AND json_extract(alternatives_json, '$.weekly_plan') IS NOT NULL ORDER BY id DESC LIMIT 1""",
                     (location_id,)).fetchone()
    if row is None:
        return None
    alternatives = json.loads(row[0])
    snapshot = alternatives.get("facility_snapshot", {})
    if any(snapshot.get(key) != location[key] for key in models.LocationFields.model_fields):
        return None
    result = alternatives["weekly_plan"]
    current = {row["id"]: row for row in db.execute(
        "SELECT id, manager_status, posture, recipients_json FROM decisions WHERE location_id = ?", (location_id,))}
    for day in result["days"]:
        for group in day.get("message_groups", []):
            decision = current.get(group["decision_id"])
            if decision:
                group["manager_status"] = decision["manager_status"]
                group["posture"] = decision["posture"]
                group["recipient_count"] = sum(row["included"] for row in json.loads(decision["recipients_json"]))
    return result


@router.post("/decisions/{decision_id}/review", response_model=models.Decision, dependencies=[Depends(mutation_guard)])
def review_recommendation(decision_id: RouteID, body: models.ReviewRequest, db: DB, manager: ManagerSession) -> dict:
    """Approval prepares a message; it never sends notifications."""
    review.review_decision(db, decision_id, manager["emp_id"], body)
    return decision(decision_id, db, manager)


app = create_app()
