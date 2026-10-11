"""AirNow's June 2026 observation service, used inside the existing AQI tool."""

import logging
import math
import os
import re
from datetime import UTC, date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

ENDPOINT = "https://www.airnowapi.org/aq/observation/current/ziplatLong"
# The API supplies the actual standard/daylight abbreviation, not an IANA zone.
UTC_OFFSETS = {"UTC": 0, "GMT": 0, "AST": -4, "ADT": -3, "EST": -5,
               "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
               "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8,
               "HST": -10, "HAST": -10, "HADT": -9, "SST": -11,
               "ChST": 10, "CHST": 10, "NST": -3.5, "NDT": -2.5}


class RedactAirNowKey(logging.Filter):
    """httpx INFO logs include request URLs; remove credential query values."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"(?i)(api_key=)[^&\s\"']+", r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(RedactAirNowKey())


def observation_time(row: dict) -> datetime:
    """New service labels the END of the measured hour; preserve that instant."""
    offset = UTC_OFFSETS[row["localTimeZone"]]
    hour = row["hourObserved"]
    if not isinstance(hour, str) or not re.fullmatch(r"(?:[01]\d|2[0-4]):00", hour):
        raise ValueError("Invalid AirNow hour")
    midnight = datetime.combine(date.fromisoformat(row["dateObserved"].strip()), datetime.min.time(),
                                timezone(timedelta(hours=offset)))
    return (midnight + timedelta(hours=int(hour[:2]))).astimezone(UTC)


def normalize_observation(row: dict, now: datetime) -> dict:
    """Keep source facts, reject missing/invalid/stale values without inventing AQI."""
    observed = observation_time(row)
    value = row["nowcastAQI"]
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise ValueError("Invalid AirNow AQI")
    if not now - timedelta(hours=3) < observed <= now:
        raise ValueError("AirNow observation is stale or future dated")
    expires = observed + timedelta(hours=3)
    return {"us_aqi": value, "observed_at": observed.isoformat(), "modeled": False,
            "measurement_period_start": (observed - timedelta(hours=1)).isoformat(),
            "valid_until": min(expires, now + timedelta(hours=1)).isoformat(),
            "pollutant": row["parameterName"], "category": row.get("aqiCategoryName"),
            "reporting_area": row["reportingAreaName"], "site_id": row.get("siteID"),
            "site_name": row.get("siteName"), "agency": row.get("reportingAgency"),
            "lookup_boundary": row.get("lookupBoundary"), "lookup_behavior": row.get("lookupBehavior"),
            "scope": "Nearby monitoring observation / NowCast; not an onsite facility sensor."}


def fetch_observations(latitude: float, longitude: float, get_json) -> dict:
    """Fetch current readings; public evidence URLs never contain the API key."""
    now = datetime.now(UTC)
    public_url = ENDPOINT + "?" + urlencode({"format": "application/json",
                                            "latitude": latitude, "longitude": longitude})
    result = {"source": "AirNow", "available": False, "modeled": False,
              "fetched_at": now.isoformat(), "source_urls": [public_url], "points": []}
    key = os.environ.get("AIRNOW_API_KEY", "").strip()
    if not key:
        return result | {"error": "AirNow observations unavailable: AIRNOW_API_KEY is not configured."}
    try:
        rows = get_json(public_url + "&" + urlencode({"api_key": key}))
        if not isinstance(rows, list) or not rows:
            raise ValueError("No AirNow observations for this location")
        points, rejected = [], 0
        for row in rows:
            try:
                points.append(normalize_observation(row, now))
            except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
                rejected += 1
        if not points:
            raise ValueError("No valid fresh AirNow observations")
        # If a source repeats a pollutant, keep its newest reading, not an old peak.
        latest = {}
        for point in sorted(points, key=lambda item: item["observed_at"]):
            latest[point["pollutant"]] = point
        return result | {"available": True, "points": list(latest.values()),
                         "rejected_rows": rejected}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        # Do not echo provider exceptions: their request URLs contain the key.
        return result | {"error": f"AirNow observations unavailable ({type(error).__name__}): no usable current reading; retry evaluation."}
