"""Seven dated policy checks, one adviser call, and separate affected-shift drafts."""

from datetime import UTC, datetime, time, timedelta
import json
import sqlite3
from zoneinfo import ZoneInfo

from app import agent, tools
from app.evaluator import save_decision
from app.db import utc_now
from app.models import Recommendation
from app.recipients import DAYS, filter_roster, load_roster, local_boundary
from app.thresholds import forecast_wind_mph, observed_aqi_context, operating_window, policy_context, timestamp

RANK = {"NORMAL": 0, "DELAYED_OPENING": 1, "EARLY_DISMISSAL": 1, "TELEWORK": 2, "CLOSED": 3}


def week_contexts(location: dict, signals: dict, now: datetime) -> tuple[list[dict], dict[str, dict]]:
    """Every date is independent; a missing forecast is never labeled NORMAL."""
    first, _ = operating_window(location, now)
    zone = ZoneInfo(location["timezone"])
    first_day = first.astimezone(zone).date()
    days, contexts = [], {}
    for offset in range(7):
        day = first_day + timedelta(days=offset)
        cursor = max(now, datetime.combine(day, time.fromisoformat(location["open_time"]), zone).astimezone(UTC))
        try:
            context = policy_context(location, signals, cursor)
            contexts[day.isoformat()] = context
            days.append({"date": day.isoformat(), "state": "COMPLETED", "posture": context["posture"],
                         "reason": "Awaiting agent explanation.", "warnings": context["warnings"],
                         "decision_ids": [], "message_groups": []})
        except (ValueError, KeyError, TypeError):
            days.append({"date": day.isoformat(), "state": "INSUFFICIENT_DATA",
                         "reason": "Insufficient source coverage for this date; no all-clear or employee draft generated."})
    return days, contexts


def impact_segments(context: dict) -> list[dict]:
    """Split disjoint/mixed hazards; never notify staff working only in a safe gap."""
    windows = [(timestamp(item["start"]), timestamp(item["end"]), item["posture"])
               for item in context.get("impact_windows", [])]
    boundaries = sorted({instant for left, right, _ in windows for instant in (left, right)})
    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        active = [posture for left, right, posture in windows if left < end and right > start]
        if not active:
            continue
        posture = max(active, key=RANK.__getitem__)
        if segments and segments[-1]["posture"] == posture and segments[-1]["end"] == start:
            segments[-1]["end"] = end
        else:
            segments.append({"posture": posture, "start": start, "end": end})
    return segments


def affected_shift_groups(roster: list[dict], location: dict, day: str, context: dict) -> list[dict]:
    """Match actual dates and intersect each hazard with each eligible employee's shift."""
    zone = ZoneInfo(location["timezone"])
    groups = []
    for segment in impact_segments(context):
        rows = filter_roster(roster, location, segment["start"].isoformat(), segment["end"].isoformat(), segment["posture"])
        shifts = {row["shift_id"]: row for row in rows if row["included"]}
        for shift_id, row in shifts.items():
            local_day = segment["start"].astimezone(zone).date()
            hours = row["weekly_hours"][DAYS[local_day.weekday()]]
            start = max(segment["start"], local_boundary(local_day, hours[0], zone))
            end = min(segment["end"], local_boundary(local_day, hours[1], zone, end=True))
            groups.append({"date": day, "shift_id": shift_id, "shift_name": row["shift_name"],
                           "posture": segment["posture"], "start": start.isoformat(), "end": end.isoformat()})
    return groups


def window_reason(group: dict, signals: dict) -> str:
    """Tailor employee text to this window using source facts, not another day's advice."""
    start, end = timestamp(group["start"]), timestamp(group["end"])
    hazards = []
    for alert in signals["weather"].get("alerts", []):
        if alert.get("status", "Actual") != "Actual" or alert.get("messageType") == "Cancel":
            continue
        try:
            if timestamp(alert.get("onset") or alert["effective"]) < end and timestamp(alert.get("ends") or alert["expires"]) > start:
                hazards.append(f"NWS {alert.get('event', 'alert')} ({alert.get('severity', 'unknown severity')})")
        except (ValueError, KeyError, TypeError):
            continue
    winds = [forecast_wind_mph(item.get("windSpeed")) for item in signals["weather"].get("forecast", [])
             if timestamp(item["startTime"]) < end and timestamp(item["endTime"]) > start]
    if any(value is not None and value >= 40 for value in winds):
        hazards.append(f"NWS forecast sustained wind up to {max(value for value in winds if value is not None):g} mph")
    aqi = [item["us_aqi"] for item in signals["air_quality"].get("forecast", [])
           if timestamp(item["observed_at"]) < end and timestamp(item["observed_at"]) + timedelta(hours=1) > start]
    if aqi and max(aqi) >= 151:
        hazards.append(f"modeled US AQI up to {max(aqi):g}")
    observed, _, _, _ = observed_aqi_context(signals["air_quality"], start, end)
    for entry in observed:
        point = entry["value"]
        if point["used_for_policy"] and point["us_aqi"] >= 151:
            hazards.append(f"AirNow observed {point['pollutant']} NowCast AQI {point['us_aqi']:g}; short-term demo precaution")
    return (f"Your {group['shift_name']} shift on {group['date']} overlaps this affected window. "
            f"Source evidence: {'; '.join(hazards)}. The app-defined demo policy recommends "
            f"{group['posture'].replace('_', ' ')} for this window.")


def save_week(db: sqlite3.Connection, location: dict, signals: dict, days: list[dict], contexts: dict, advice: dict) -> dict:
    """Commit all validated day/shift drafts together, keeping today's status unchanged."""
    with db:
        roster = load_roster(db, location["id"])
        for day in days:
            if day["state"] != "COMPLETED":
                continue
            date, result = day["date"], advice[day["date"]]
            context = contexts[date]
            day["reason"] = result.reason
            groups = affected_shift_groups(roster, location, date, context)
            if not groups:
                rows = filter_roster(roster, location, result.affected_start.isoformat(), result.affected_end.isoformat(),
                                     result.posture, impact_windows=context.get("impact_windows"))
                decision_id = save_decision(db, location, result, signals, context,
                                           {"date": date, "kind": "day_assessment"}, recipients=rows)
                day["decision_ids"].append(decision_id)
            for group in groups:
                grouped = Recommendation(**(result.model_dump() | {"posture": group["posture"],
                    "affected_start": group["start"], "affected_end": group["end"], "reason": window_reason(group, signals)}))
                rows = filter_roster(roster, location, group["start"], group["end"], group["posture"], shift_id=group["shift_id"])
                decision_id = save_decision(db, location, grouped, signals, context, group, recipients=rows)
                day["decision_ids"].append(decision_id)
                saved_rows = json.loads(db.execute('SELECT recipients_json FROM decisions WHERE id = ?',
                                                  (decision_id,)).fetchone()[0])
                day["message_groups"].append(group | {"decision_id": decision_id,
                    "recipient_count": sum(row['included'] for row in saved_rows),
                    "manager_status": "PENDING"})
        result = {"state": "COMPLETED", "days": days, "evaluated_at": utc_now(),
                  "message": "Seven dates checked. Only affected date/shift groups have recipient drafts. Review each before approval; no emails sent."}
        first_id = next(day["decision_ids"][0] for day in days if day.get("decision_ids"))
        row = db.execute("SELECT alternatives_json FROM decisions WHERE id = ?", (first_id,)).fetchone()
        alternatives = json.loads(row[0]) | {"weekly_plan": result}
        db.execute("UPDATE decisions SET alternatives_json = ? WHERE id = ?", (json.dumps(alternatives), first_id))
    return result


def run_weekly_evaluation(db: sqlite3.Connection, location: dict) -> dict:
    """Fetch the same two sources once and invoke one Strands adviser once."""
    signals = tools.fetch_signals(location["latitude"], location["longitude"])
    days, contexts = week_contexts(location, signals, datetime.now(UTC))
    if not contexts:
        return {"state": "INSUFFICIENT_DATA", "days": days, "message": "No dates have sufficient source coverage; no drafts saved."}
    advice = agent.recommend_week(location, contexts)
    return save_week(db, location, signals, days, contexts, advice)
