"""Date-aware, half-open shift matching in each facility's IANA timezone."""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from typing import get_args
from zoneinfo import ZoneInfo

from app.models import Posture
from app.thresholds import timestamp

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def local_boundary(day: date, clock: str, zone: ZoneInfo, end: bool = False) -> datetime:
    """Reject nonexistent DST times; use the widest genuine interval on fall-back."""
    wall = datetime.combine(day, time.fromisoformat(clock))
    local = wall.replace(tzinfo=zone, fold=int(end))
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != wall:
        raise ValueError("Shift boundary falls in a daylight-saving gap")
    return local.astimezone(UTC)


def scheduled_overlap(location: dict, hours: dict, start: datetime, end: datetime) -> bool:
    """Intersect actual dated shifts, office hours and [start, end), including midnight windows."""
    zone = ZoneInfo(location["timezone"])
    day, final_day = start.astimezone(zone).date(), end.astimezone(zone).date()
    while day <= final_day:
        entry = hours.get(DAYS[day.weekday()])
        if entry is not None:
            if (not isinstance(entry, list) or len(entry) != 2
                    or not all(isinstance(value, str) for value in entry) or entry[1] <= entry[0]):
                raise ValueError("Only valid same-day shift templates are supported")
            shift_start = local_boundary(day, entry[0], zone)
            shift_end = local_boundary(day, entry[1], zone, end=True)
            office_start = local_boundary(day, location["open_time"], zone)
            office_end = local_boundary(day, location["close_time"], zone, end=True)
            if max(start, shift_start, office_start) < min(end, shift_end, office_end):
                return True
        day += timedelta(days=1)
    return False


def load_roster(db: sqlite3.Connection, location_id: int, shift_id: int | None = None) -> list[dict]:
    """One indexed roster query; decode each distinct shift once, with no global cache."""
    query = """SELECT u.*, s.name AS shift_name, s.weekly_hours_json FROM users u
               LEFT JOIN shifts s ON s.id = u.shift_id WHERE u.location_id = ?"""
    parameters = [location_id]
    if shift_id is not None:
        query += " AND u.shift_id = ?"
        parameters.append(shift_id)
    rows = db.execute(query + " ORDER BY u.emp_id", parameters).fetchall()
    schedules, roster = {}, []
    for row in rows:
        employee = dict(row)
        raw = employee.pop("weekly_hours_json")
        if employee["shift_id"] not in schedules:
            try:
                hours = json.loads(raw) if raw is not None else None
                if hours is not None and not isinstance(hours, dict):
                    raise ValueError("Shift schedule must be a weekday object")
                schedules[employee["shift_id"]] = (hours, False)
            except (ValueError, TypeError):
                schedules[employee["shift_id"]] = (None, True)
        hours, invalid = schedules[employee["shift_id"]]
        roster.append(employee | {"weekly_hours": hours, "_invalid_shift": invalid})
    return roster


def selection_intervals(start: str, end: str, impact_windows: list[dict] | None) -> list[tuple[datetime, datetime]]:
    """Use exact UTC intervals, merging overlaps but preserving safe gaps."""
    left, right = timestamp(start), timestamp(end)
    if left >= right or right - left > timedelta(days=3):
        raise ValueError("Recipient window must be ordered and no longer than three days")
    windows = impact_windows or [{"start": start, "end": end}]
    intervals = []
    for window in windows:
        try:
            begins, ends = timestamp(window["start"]), timestamp(window["end"])
        except (KeyError, TypeError):
            raise ValueError("Impact windows require timezone-aware start and end times") from None
        if begins >= ends:
            raise ValueError("Impact window end must follow its start")
        begins, ends = max(left, begins), min(right, ends)
        if begins < ends:
            intervals.append((begins, ends))
    merged = []
    for begins, ends in sorted(intervals):
        if merged and begins <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(ends, merged[-1][1]))
        else:
            merged.append((begins, ends))
    return merged


def filter_roster(
    roster: list[dict], location: dict, start: str, end: str, posture: str,
    scope: str = "affected", include_ids: list[int] | None = None, exclude_ids: list[int] | None = None,
    shift_id: int | None = None, impact_windows: list[dict] | None = None,
) -> list[dict]:
    """Compare only posture and canonical times; explanations/evidence never become SQL."""
    if posture not in get_args(Posture) or scope not in ("affected", "all_staff"):
        raise ValueError("Invalid posture or recipient scope")
    intervals = selection_intervals(start, end, impact_windows)
    rows = [row for row in roster if row["location_id"] == location["id"]]
    included, excluded = set(include_ids or []), set(exclude_ids or [])
    if included & excluded or not (included | excluded) <= {row["emp_id"] for row in rows}:
        raise ValueError("Selections must be disjoint employee IDs belonging to this facility")
    matches, result = {}, []
    for row in rows:
        employee = dict(row)
        invalid = employee.pop("_invalid_shift")
        hours = employee["weekly_hours"]
        if employee["shift_id"] not in matches:
            try:
                overlap = bool(hours and any(scheduled_overlap(location, hours, left, right) for left, right in intervals))
                matches[employee["shift_id"]] = (overlap, invalid)
            except (ValueError, TypeError, AttributeError):
                matches[employee["shift_id"]] = (False, True)
        overlap, invalid = matches[employee["shift_id"]]
        if shift_id is not None and employee['shift_id'] != shift_id:
            overlap = False
        reason = ("invalid_shift" if invalid and scope == "affected" and employee["emp_id"] not in included
                  else eligibility(employee, overlap, posture, scope, included))
        if invalid:
            employee["weekly_hours"] = None
        if employee["emp_id"] in excluded:
            reason = "manager_excluded"
        eligible = reason in ("scheduled_on_site", "facility_wide", "manager_selected")
        result.append(employee | {"included": eligible, "reason": reason,
            "requires_manager_review": bool(eligible and employee["is_essential"]),
            "warning": "Essential personnel: manager must explicitly confirm instructions." if eligible and employee["is_essential"] else None})
    return result


def select_recipients(
    db: sqlite3.Connection, location: dict, start: str, end: str, posture: str,
    scope: str = "affected", include_ids: list[int] | None = None, exclude_ids: list[int] | None = None,
    shift_id: int | None = None, impact_windows: list[dict] | None = None,
) -> list[dict]:
    """Fresh one-query selection for an individual draft or manager review."""
    return filter_roster(load_roster(db, location["id"]), location, start, end, posture,
                         scope, include_ids, exclude_ids, shift_id, impact_windows)


def eligibility(employee: dict, overlap: bool, posture: str, scope: str, selected: set[int]) -> str:
    """Wider safety notices are explicit; ordinary commute notices exclude remote/unscheduled staff."""
    if employee["emp_id"] in selected:
        return "manager_selected"
    if scope == "all_staff":
        return "facility_wide"
    if employee["is_manager"]:
        return "manager"
    if posture == "NORMAL":
        return "no_disruption"
    if not overlap:
        return "not_scheduled"
    if employee["is_teleworking"]:
        return "already_remote"
    return "manager_selected" if employee["emp_id"] in selected else "scheduled_on_site"
