from datetime import UTC, datetime

import httpx
import pytest

from app import tools


def test_nws_discovers_official_sources_and_preserves_alert_fields(monkeypatch):
    calls = []

    def source(url, headers=None):
        calls.append((url, headers))
        if "/points/" in url:
            return {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly"}}
        if "/alerts/" in url:
            return {"features": [{"id": "https://api.weather.gov/alerts/test-only", "properties": {
                "event": "Tornado Warning", "severity": "Extreme", "urgency": "Immediate",
                "certainty": "Observed", "sent": "2026-10-10T10:00:00+00:00",
                "description": "TEST ONLY hazardous conditions", "instruction": "TEST ONLY instructions",
                "areaDesc": "TEST ONLY county", "status": "Actual", "messageType": "Alert"}}]}
        return {"properties": {"updateTime": "2026-10-10T10:00:00+00:00", "periods": [
            {"startTime": "2026-10-10T10:00:00+00:00", "endTime": "2026-10-10T11:00:00+00:00", "shortForecast": "Rain"}]}}

    monkeypatch.setattr(tools, "get_json", source)
    result = tools.get_nws_weather(41.8781, -87.6298)
    assert result["available"] is True
    assert result["alerts"][0]["certainty"] == "Observed"
    assert result["alerts"][0]["description"] == "TEST ONLY hazardous conditions"
    assert result["alerts"][0]["instruction"] == "TEST ONLY instructions"
    assert result["alerts"][0]["areaDesc"] == "TEST ONLY county"
    assert result["forecast"][0]["shortForecast"] == "Rain"
    assert all(url.startswith("https://api.weather.gov/") for url, _ in calls)
    assert all(headers["User-Agent"] for _, headers in calls)


@pytest.mark.parametrize("discovery", ["https://evil.example/forecast", "http://api.weather.gov/forecast"])
def test_nws_rejects_untrusted_discovery(monkeypatch, discovery):
    monkeypatch.setattr(tools, "get_json", lambda *args: {"properties": {"forecastHourly": discovery}})
    assert tools.get_nws_weather(41, -87)["available"] is False


def test_alerts_survive_forecast_failure_and_alert_only_refresh_skips_forecast(monkeypatch):
    calls = []
    def source(url, headers=None):
        calls.append(url)
        if "/alerts/" in url:
            return {"features": [{"id": "https://api.weather.gov/alerts/test-only", "properties": {
                "event": "Unknown Future Alert", "severity": "Unknown", "instruction": "TEST ONLY review this alert"}}]}
        raise httpx.ReadTimeout("TEST ONLY forecast outage")
    monkeypatch.setattr(tools, "get_json", source)
    snapshot = tools.get_nws_weather(30.1766, -85.8055)
    assert snapshot["available"] is False and snapshot["alerts_available"] is True
    assert snapshot["forecast_available"] is False and snapshot["forecast_error"]
    assert snapshot["alerts"][0]["event"] == "Unknown Future Alert"
    calls.clear()
    snapshot = tools.get_nws_weather(30.1766, -85.8055, include_forecast=False)
    assert snapshot["alerts_available"] is True
    assert len(calls) == 1 and "/alerts/active?point=30.1766,-85.8055" in calls[0]


def test_alert_outage_is_not_a_successful_empty_alert_list(monkeypatch):
    def source(url, headers=None):
        if "/alerts/" in url:
            raise httpx.ReadTimeout("TEST ONLY alerts outage")
        if "/points/" in url:
            return {"properties": {"forecastHourly": "https://api.weather.gov/test"}}
        return {"properties": {"periods": [{"startTime": "2026-10-10T10:00:00Z", "endTime": "2026-10-10T11:00:00Z"}]}}
    monkeypatch.setattr(tools, "get_json", source)
    snapshot = tools.get_nws_weather(41, -87)
    assert snapshot["forecast_available"] is True
    assert snapshot["available"] is False and snapshot["alerts_available"] is False and snapshot["alerts_error"]


def test_aqi_is_modeled_and_null_points_are_not_fabricated(monkeypatch):
    stamp = int(datetime.now(UTC).timestamp())
    monkeypatch.setattr(tools, "get_json", lambda *args: {"hourly": {
        "time": [stamp, stamp + 3600], "us_aqi": [175, None], "pm2_5": [61, None]}})
    result = tools.get_air_quality(41, -87)
    assert result["available"] is True and result["modeled"] is True
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["us_aqi"] == 175
    assert result["forecast"][0]["observed_at"].endswith("+00:00")
    assert result["forecast"][0]["pm10"] is None


@pytest.mark.parametrize("function", [tools.get_nws_weather, tools.get_air_quality])
def test_timeout_and_invalid_coordinates_are_unavailable(monkeypatch, function):
    def timeout(*args):
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(tools, "get_json", timeout)
    assert function(41, -87)["available"] is False
    assert function(100, -87)["available"] is False


def test_aqi_empty_or_invalid_data_is_unavailable(monkeypatch):
    monkeypatch.setattr(tools, "get_json", lambda *args: {"hourly": {"time": [], "us_aqi": []}})
    assert tools.get_air_quality(41, -87)["available"] is False
    monkeypatch.setenv("AQI_API_URL", "https://evil.example/")
    assert tools.get_air_quality(41, -87)["available"] is False


def test_http_retries_only_transient_errors(monkeypatch):
    original = httpx.Client
    codes = iter([503, 429, 200])
    transport = httpx.MockTransport(lambda request: httpx.Response(next(codes), json={"ok": True}))
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    monkeypatch.setattr(tools.time, "sleep", lambda seconds: None)
    assert tools.get_json("https://api.weather.gov/test") == {"ok": True}
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        tools.get_json("https://api.weather.gov/test")


def test_tools_retain_forecasts_beyond_three_days_and_request_seven(monkeypatch):
    from datetime import timedelta
    from urllib.parse import parse_qs, urlparse
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    periods = [{'startTime': (now + timedelta(hours=i)).isoformat(),
                'endTime': (now + timedelta(hours=i+1)).isoformat(), 'windSpeed': '45 mph'} for i in range(180)]
    def source(url, headers=None):
        if '/alerts/' in url:
            return {'features': []}
        if '/points/' in url:
            return {'properties': {'forecastHourly': 'https://api.weather.gov/test-hourly'}}
        if 'api.weather.gov' in url:
            return {'properties': {'periods': periods}}
        assert parse_qs(urlparse(url).query)['forecast_days'] == ['7']
        return {'hourly': {'time': [int((now + timedelta(hours=i)).timestamp()) for i in range(180)], 'us_aqi': [40]*180}}
    monkeypatch.setattr(tools, 'get_json', source)
    weather = tools.get_nws_weather(41, -87)
    aqi = tools.get_air_quality(41, -87)
    assert len(weather['forecast']) == 168 and weather['forecast'][-1]['windSpeed'] == '45 mph'
    assert len(aqi['forecast']) >= 168
    assert datetime.fromisoformat(aqi['forecast'][-1]['observed_at']) > now + timedelta(days=6)
