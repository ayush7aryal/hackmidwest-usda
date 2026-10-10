"""The agent's only two tools: bounded, read-only requests to public data sources."""

import math
import os
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx
from strands import tool

from app.db import utc_now

AQI_ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"


def get_json(url: str, headers: dict | None = None) -> dict:
    """Retry timeouts, throttling and server errors; never follow arbitrary redirects."""
    with httpx.Client(timeout=httpx.Timeout(10, connect=5), headers=headers) as client:
        for attempt in range(3):
            try:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Source returned a non-object response")
                return data
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
                if isinstance(error, httpx.HTTPStatusError):
                    code = error.response.status_code
                    if code != 429 and code < 500:
                        raise
                if attempt == 2:
                    raise
                time.sleep(0.25 * 2 ** attempt)
    raise RuntimeError("Source request did not complete")


def valid_coordinates(latitude: float, longitude: float) -> None:
    """Tools are independently validated because a model can supply their arguments."""
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Latitude/longitude must be finite and within geographic bounds")


def nws_url(value: str) -> str:
    """Only permit NWS discovery links to the official HTTPS API host."""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "api.weather.gov":
        raise ValueError("NWS returned an invalid discovery URL")
    return value


def unavailable(source: str, urls: list[str], error: Exception) -> dict:
    """Expose an actionable failure type without leaking response bodies or secrets."""
    return {"source": source, "available": False, "fetched_at": utc_now(),
            "source_urls": urls, "error": f"{source} unavailable ({type(error).__name__}); retry evaluation."}


@tool
def get_nws_weather(latitude: float, longitude: float, include_forecast: bool = True) -> dict:
    """Fetch actual US NWS hourly forecasts and active alerts for these coordinates.

    Args:
        latitude: Facility latitude in decimal degrees.
        longitude: Facility longitude in decimal degrees.
        include_forecast: False for a fast alert-only manager refresh.
    """
    point = f"https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}"
    alerts_url = f"https://api.weather.gov/alerts/active?point={latitude:.4f},{longitude:.4f}"
    urls = [point, alerts_url] if include_forecast else [alerts_url]
    headers = {"User-Agent": os.environ.get("NWS_USER_AGENT", "FacilityOperationsAI/0.1 (demo@example.com)"),
               "Accept": "application/geo+json"}
    result = {"source": "NWS", "available": False, "fetched_at": utc_now(),
              "source_urls": urls, "alerts_available": False, "forecast_available": False,
              "alerts": [], "forecast": []}
    try:
        valid_coordinates(latitude, longitude)
    except ValueError as error:
        return result | {"error": unavailable("NWS", urls, error)["error"]}
    try:
        alerts = []
        for feature in get_json(alerts_url, headers)["features"]:
            item = feature["properties"]
            alerts.append({key: item.get(key) for key in
                           ("event", "severity", "urgency", "certainty", "effective", "onset", "ends", "expires", "sent", "headline",
                            "description", "instruction", "areaDesc", "response", "status", "messageType", "category", "senderName")}
                          | {"url": nws_url(feature["id"])})
        result.update(alerts=alerts, alerts_available=True)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
        result["alerts_error"] = unavailable("NWS alerts", urls, error)["error"]
    if include_forecast:
        try:
            properties = get_json(point, headers)["properties"]
            forecast_url = nws_url(properties.get("forecastHourly") or properties["forecast"])
            urls.append(forecast_url)
            forecast = get_json(forecast_url, headers)["properties"]
            periods = [{key: period.get(key) for key in
                        ("startTime", "endTime", "temperature", "temperatureUnit", "windSpeed", "shortForecast")}
                       for period in forecast["periods"][:168]]
            if not periods:
                raise ValueError("NWS returned no forecast periods")
            result.update(forecast=periods, forecast_available=True,
                          forecast_updated_at=forecast.get("updateTime"))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
            result["forecast_error"] = unavailable("NWS forecast", urls, error)["error"]
    result["available"] = result["alerts_available"] and result["forecast_available"]
    result["fetched_at"] = utc_now()
    return result


@tool
def get_air_quality(latitude: float, longitude: float) -> dict:
    """Fetch modeled US AQI and particulate forecasts from configured Open-Meteo.

    Args:
        latitude: Facility latitude in decimal degrees.
        longitude: Facility longitude in decimal degrees.
    """
    endpoint = os.environ.get("AQI_API_URL", AQI_ENDPOINT)
    url = endpoint + "?" + urlencode({"latitude": latitude, "longitude": longitude,
        "hourly": "us_aqi,pm2_5,pm10", "timezone": "UTC", "timeformat": "unixtime", "forecast_days": 7})
    try:
        valid_coordinates(latitude, longitude)
        if endpoint != AQI_ENDPOINT:
            raise ValueError("AQI_API_URL must use the documented Open-Meteo endpoint")
        hourly = get_json(url)["hourly"]
        now = datetime.now(UTC)
        points = []
        for index, timestamp in enumerate(hourly["time"]):
            observed = datetime.fromtimestamp(timestamp, UTC)
            aqi = hourly["us_aqi"][index]
            if now - timedelta(hours=1) <= observed <= now + timedelta(days=7) and aqi is not None:
                if not isinstance(aqi, (int, float)) or not math.isfinite(aqi) or aqi < 0:
                    raise ValueError("AQI source returned an invalid value")
                points.append({"observed_at": observed.isoformat(), "us_aqi": aqi,
                               "pm2_5": hourly.get("pm2_5", [None] * len(hourly["time"]))[index],
                               "pm10": hourly.get("pm10", [None] * len(hourly["time"]))[index]})
        if not points:
            raise ValueError("No current AQI forecast is available")
        return {"source": "Open-Meteo / CAMS", "available": True, "modeled": True,
                "fetched_at": utc_now(), "source_urls": [url], "forecast": points,
                "pollutant_units": "µg/m³", "attribution": "Open-Meteo and Copernicus Atmosphere Monitoring Service"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        return unavailable("Open-Meteo / CAMS", [url], error)
