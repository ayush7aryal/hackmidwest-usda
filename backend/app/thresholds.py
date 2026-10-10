"""Versioned DEMO operating policy, anchored to official hazard/AQI categories.

Posture actions and timing rules are app policy, not government closure guidance.
"""

from datetime import UTC, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

POLICY_VERSION = "demo-office-v3"
AQI_GUIDANCE = "https://www.airnow.gov/aqi/aqi-basics/"
NWS_GUIDANCE = "https://www.weather.gov/documentation/services-web-api"


def timestamp(value: str) -> datetime:
    """Require explicit timezone offsets on every external instant."""
    if not isinstance(value, str):
        raise ValueError("Timezone-aware timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return parsed.astimezone(UTC)


def operating_window(location: dict, now: datetime) -> tuple[datetime, datetime]:
    """Evaluate the current or next same-day opening, expressed in facility time."""
    zone = ZoneInfo(location["timezone"])
    local_now = now.astimezone(zone)
    day = local_now.date()
    close = datetime.combine(day, time.fromisoformat(location["close_time"]), zone)
    if local_now >= close:
        day += timedelta(days=1)
    start = datetime.combine(day, time.fromisoformat(location["open_time"]), zone)
    end = datetime.combine(day, time.fromisoformat(location["close_time"]), zone)
    return max(start.astimezone(UTC), now), end.astimezone(UTC)


def rule(name: str, threshold: str, actual: str, margin: str, source: str) -> dict:
    """Persist the exact trigger, provenance and policy version with every decision."""
    return {"rule": name, "threshold": threshold, "actual": actual, "margin": margin,
            "provenance": source, "policy_version": POLICY_VERSION, "kind": "app-defined demo policy"}


def weather_candidate(alert: dict, start: datetime, end: datetime) -> tuple[str, datetime, datetime] | None:
    """Map overlapping severe alerts to graded advice; weak/vague alerts remain review evidence."""
    severity, urgency, certainty = (alert.get(key) for key in ("severity", "urgency", "certainty"))
    if severity not in ("Severe", "Extreme") or urgency not in ("Immediate", "Expected") or certainty not in ("Observed", "Likely"):
        return None
    begins = timestamp(alert.get("onset") or alert["effective"])
    ends = timestamp(alert.get("ends") or alert["expires"])
    left, right = max(start, begins), min(end, ends)
    if left >= right:
        return None
    if severity == "Extreme" and urgency == "Immediate":
        return "CLOSED", left, right
    midpoint = start + (end - start) / 2
    if right <= midpoint:
        return "DELAYED_OPENING", start, right
    if left >= midpoint:
        return "EARLY_DISMISSAL", left, end
    return "TELEWORK", left, right


def covers_window(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    """Do not hide internal gaps by checking only the first and last forecast times."""
    cursor = start
    for left, right in sorted(intervals):
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return False


def forecast_wind_mph(value: str | None) -> float | None:
    """Read NWS sustained wind/ranges; do not guess units or treat gusts as sustained."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?(?: to \d+(?:\.\d+)?)? mph", value):
        return None
    return max(float(number) for number in re.findall(r"\d+(?:\.\d+)?", value))


def assess_weather_alert(alert: dict, start: datetime, end: datetime) -> tuple[tuple[str, datetime, datetime] | None, str, bool]:
    """Explain every alert, including advisories that do not change an office schedule."""
    if alert.get("status") in ("Test", "Exercise", "Draft", "System") or alert.get("messageType") == "Cancel":
        return None, "Test, exercise or canceled alert; no office schedule action.", False
    if alert.get("status", "Actual") != "Actual":
        return None, "Alert status is unknown; manager must check the NWS source.", True
    try:
        begins = timestamp(alert.get("onset") or alert["effective"])
        ends = timestamp(alert.get("ends") or alert["expires"])
        if begins >= ends:
            raise ValueError("Invalid alert interval")
        if begins >= end or ends <= start:
            return None, "Outside the evaluated office window; no schedule change for this window.", False
        candidate = weather_candidate(alert, start, end)
    except (ValueError, KeyError, TypeError):
        return None, "Alert timing is incomplete; manager must check the NWS source.", True
    if candidate:
        return candidate, f"Meets demo office trigger: {candidate[0]}.", False
    return None, ("Active during the office window, but below the Severe/Extreme, Immediate/Expected, "
                  "Observed/Likely schedule-change trigger. Manager must review local impacts."), True


def policy_context(location: dict, signals: dict, now: datetime) -> dict:
    """Derive bounded, evidence-backed policy inputs independently of the model."""
    start, end = operating_window(location, now)
    evidence, thresholds, candidates, warnings, weather_alerts = [], [], [], [], []
    nws, aqi = signals["weather"], signals["air_quality"]
    weather_ok = False
    alerts_ok = nws.get("alerts_available", nws["available"])
    if nws.get("forecast_available", nws["available"]):
        periods = [p for p in nws["forecast"] if timestamp(p["startTime"]) < end and timestamp(p["endTime"]) > start]
        # A short or stale forecast must not imply an all-clear for the whole office window.
        forecast_ok = covers_window([(timestamp(p["startTime"]), timestamp(p["endTime"])) for p in periods], start, end)
        weather_ok = forecast_ok and alerts_ok
        if periods:
            evidence.append({"source": "NWS", "url": nws["source_urls"][-1],
                "observed_at": nws.get("forecast_updated_at") or nws["fetched_at"],
                "value": {"forecast": periods, "active_alert_count": len(nws["alerts"]) if alerts_ok else None}})
        for period in periods:
            wind = forecast_wind_mph(period.get("windSpeed"))
            if wind is not None and wind >= 40:
                candidates.append(("TELEWORK", max(start, timestamp(period["startTime"])), min(end, timestamp(period["endTime"]))))
                thresholds.append(rule("Forecast sustained wind", "TELEWORK >=40 mph (app-defined demo trigger)",
                    f"{period['windSpeed']} at {period['startTime']}", f"{wind - 40:+g} mph from trigger", NWS_GUIDANCE))
            if "thunderstorm" in (period.get("shortForecast") or "").lower():
                warnings.append(f"Forecast thunderstorms at {period['startTime']}; manager must review local timing and impacts.")
    if alerts_ok:
        for alert in nws["alerts"]:
            candidate, assessment, needs_review = assess_weather_alert(alert, start, end)
            # Keep every alert visible to the agent, even when no automatic action applies.
            compact = alert | {key: (alert.get(key) or "")[:limit]
                               for key, limit in (("description", 500), ("instruction", 300))}
            weather_alerts.append(compact | {"demo_policy_assessment": assessment})
            # Long descriptions inform the explanation; source facts are compact and exact.
            evidence.append({"source": "NWS", "url": alert["url"],
                "observed_at": alert.get("sent") or nws["fetched_at"],
                "value": {key: alert.get(key) for key in ("event", "severity", "urgency", "certainty")}})
            thresholds.append(rule("NWS alert assessment", "Severe/Extreme; Immediate/Expected; Observed/Likely; overlapping office hours",
                f"{alert.get('event')}: {alert.get('severity')}; {alert.get('urgency')}; {alert.get('certainty')}",
                assessment, NWS_GUIDANCE))
            if needs_review:
                warnings.append(f"{alert.get('event', 'NWS alert')}: {assessment} NORMAL means no automatic office schedule change, not hazard-free weather.")
            if candidate:
                candidates.append(candidate)
    aqi_ok = False
    if aqi["available"]:
        points = [p for p in aqi["forecast"] if timestamp(p["observed_at"]) < end
                  and timestamp(p["observed_at"]) + timedelta(hours=1) > start]
        aqi_ok = covers_window([(timestamp(p["observed_at"]), timestamp(p["observed_at"]) + timedelta(hours=1)) for p in points], start, end)
        if points:
            worst = max(points, key=lambda p: p["us_aqi"])
            evidence.append({"source": aqi["source"], "url": aqi["source_urls"][0],
                             "observed_at": worst["observed_at"], "value": worst | {"modeled": True}})
            value = worst["us_aqi"]
            thresholds.append(rule("Modeled US AQI", "TELEWORK >=151; CLOSED >=301",
                str(value), f"{value - 151:+g} from telework trigger", AQI_GUIDANCE))
            if value >= 151:
                candidates.extend(("CLOSED" if point["us_aqi"] >= 301 else "TELEWORK",
                                   max(start, timestamp(point["observed_at"])),
                                   min(end, timestamp(point["observed_at"]) + timedelta(hours=1)))
                                  for point in points if point["us_aqi"] >= 151)
    if not weather_ok:
        warnings.append("NWS weather coverage unavailable or incomplete; no weather all-clear.")
    if not aqi_ok:
        warnings.append("Modeled air-quality coverage unavailable or incomplete.")
    if not candidates and not weather_ok:
        raise ValueError("INSUFFICIENT_DATA: NWS coverage cannot support an all-clear and AQI alone cannot justify disruption.")
    if not thresholds:
        thresholds.append(rule("No severe alert trigger", "No overlapping qualifying alert", "No trigger", "Below demo action criteria", NWS_GUIDANCE))
    ranking = {"NORMAL": 0, "DELAYED_OPENING": 1, "EARLY_DISMISSAL": 1, "TELEWORK": 2, "CLOSED": 3}
    posture, left, right = max(candidates, key=lambda item: ranking[item[0]], default=("NORMAL", start, end))
    # Multiple hazards at equal strength require the union, rather than dropping one window.
    equal = [item for item in candidates if ranking[item[0]] == ranking[posture]]
    if len(equal) > 1:
        left, right = min(item[1] for item in equal), max(item[2] for item in equal)
        if {item[0] for item in equal} == {"DELAYED_OPENING", "EARLY_DISMISSAL"}:
            posture = "TELEWORK"
    return {"posture": posture, "affected_start": left.isoformat(), "affected_end": right.isoformat(),
            "evidence": evidence, "thresholds": thresholds, "warnings": warnings, "weather_alerts": weather_alerts,
            "impact_windows": [{"posture": action, "start": begins.isoformat(), "end": ends.isoformat()}
                               for action, begins, ends in candidates],
            "confidence_cap": 0.5 if warnings else 0.8, "policy_version": POLICY_VERSION}
