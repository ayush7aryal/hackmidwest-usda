"""Synthetic source tests; no network credentials or actual email delivery."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import airnow, tools
from app.agent import briefing_context
from app.recipients import filter_roster
from app.thresholds import policy_context
from app.weekly import week_contexts, window_reason
from test_policy import LOCATION, NOW, signals


def row(**changes):
    return {"dateObserved": "2026-10-12", "hourObserved": "09:00", "localTimeZone": "CDT",
            "parameterName": "PM2.5", "nowcastAQI": 175, "reportingAreaName": "Synthetic Chicago",
            "siteName": "Synthetic monitor", "siteID": "test-site", "lookupBoundary": "50 Miles",
            "aqiCategoryName": "Unhealthy"} | changes


def observation_snapshot(value=175, fetched=None):
    fetched = fetched or NOW + timedelta(hours=2, minutes=20)
    point = airnow.normalize_observation(row(nowcastAQI=value), fetched)
    return {"available": True, "source": "AirNow", "fetched_at": fetched.isoformat(),
            "points": [point], "source_urls": [airnow.ENDPOINT]}


@pytest.mark.parametrize("zone,hour,expected", [
    ("CDT", "00:00", "2026-10-12T05:00:00+00:00"),
    ("CST", "24:00", "2026-10-13T06:00:00+00:00"),
    ("PDT", "09:00", "2026-10-12T16:00:00+00:00"),
    ("HST", "09:00", "2026-10-12T19:00:00+00:00"),
])
def test_observation_end_hour_converts_to_utc_without_dst_guessing(zone, hour, expected):
    assert airnow.observation_time(row(localTimeZone=zone, hourObserved=hour)).isoformat() == expected


@pytest.mark.parametrize("changes", [
    {"nowcastAQI": None}, {"nowcastAQI": -1}, {"nowcastAQI": True}, {"nowcastAQI": float("nan")},
    {"dateObserved": "2026-10-11"}, {"hourObserved": "10:00"}, {"hourObserved": "24:30"},
    {"hourObserved": "25:00"}, {"localTimeZone": "unknown"},
])
def test_invalid_stale_and_future_observations_are_excluded(changes):
    with pytest.raises((ValueError, KeyError)):
        airnow.normalize_observation(row(**changes), NOW + timedelta(hours=2, minutes=20))


def test_latest_pollutants_survive_bad_rows_and_keys_never_enter_evidence(monkeypatch):
    monkeypatch.setenv("AIRNOW_API_KEY", "synthetic-private-key")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    fresh = row(dateObserved=now.date().isoformat(), hourObserved=now.strftime('%H:00'), localTimeZone="UTC")
    old = now - timedelta(hours=1)
    def source(url):
        assert '/observation/current/ziplatLong?' in url
        assert 'api_key=synthetic-private-key' in url and 'distance=' not in url
        return [fresh, row(nowcastAQI=-1), fresh | {"nowcastAQI": 49, "parameterName": "OZONE"},
                fresh | {"dateObserved": old.date().isoformat(), "hourObserved": old.strftime('%H:00'), "nowcastAQI": 501}]
    result = airnow.fetch_observations(41.8, -87.6, source)
    assert result['available'] and result['rejected_rows'] == 1
    assert {p['pollutant']: p['us_aqi'] for p in result['points']} == {'PM2.5': 175, 'OZONE': 49}
    assert 'synthetic-private-key' not in json.dumps(result)
    assert 'api_key=' not in result['source_urls'][0]


@pytest.mark.parametrize('payload', [[], {}, {'WebServiceError': [{'Message': 'No observations'}]}, [row(nowcastAQI=-1)]])
def test_empty_error_and_invalid_payloads_are_unavailable(monkeypatch, payload):
    monkeypatch.setenv('AIRNOW_API_KEY', 'synthetic-key')
    assert not airnow.fetch_observations(41, -87, lambda url: payload)['available']


def test_unconfigured_key_never_calls_provider():
    result = airnow.fetch_observations(41, -87, lambda url: pytest.fail('No request without key'))
    assert not result['available'] and 'not configured' in result['error']


def test_http_error_and_info_logs_do_not_leak_key(monkeypatch, caplog):
    monkeypatch.setenv('AIRNOW_API_KEY', 'synthetic-private-key')
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    monkeypatch.setattr(tools.httpx, 'Client', lambda **kw: original(transport=transport, **kw))
    with caplog.at_level(logging.INFO, logger='httpx'):
        result = airnow.fetch_observations(41, -87, tools.get_json)
    assert not result['available']
    assert 'synthetic-private-key' not in json.dumps(result) + caplog.text
    assert '[REDACTED]' in caplog.text


@pytest.mark.parametrize('observed_ok,forecast_ok', [(True, False), (False, True), (False, False), (True, True)])
def test_sources_fail_independently_inside_one_aqi_tool(monkeypatch, observed_ok, forecast_ok):
    monkeypatch.setattr(tools, 'get_aqi_forecast', lambda *args: {'available': forecast_ok, 'forecast': []})
    monkeypatch.setattr(tools, 'fetch_observations', lambda *args: {'available': observed_ok, 'points': []})
    result = tools.get_air_quality(41, -87)
    assert result['available'] == (observed_ok or forecast_ok)
    assert result['forecast_available'] == forecast_ok
    assert result['observations']['available'] == observed_ok


@pytest.mark.parametrize('value,posture', [(50, 'NORMAL'), (150, 'NORMAL'), (151, 'TELEWORK'), (301, 'CLOSED'), (501, 'CLOSED')])
def test_observed_aqi_uses_precise_bounded_current_window_and_agent_facts(value, posture):
    now = NOW + timedelta(hours=2, minutes=20)
    data = signals()
    data['air_quality']['observations'] = observation_snapshot(value, now)
    context = policy_context(LOCATION, data, now)
    assert context['posture'] == posture
    observed = briefing_context(context)['observed_air_quality'][0]
    assert observed['us_aqi'] == value and observed['modeled'] is False
    if value >= 151:
        assert context['affected_start'] == now.isoformat()
        assert context['affected_end'] == (now + timedelta(hours=1)).isoformat()
        group = {'start': context['affected_start'], 'end': context['affected_end'],
                 'date': '2026-10-12', 'shift_name': 'Synthetic shift', 'posture': posture}
        assert f'AirNow observed PM2.5 NowCast AQI {value}' in window_reason(group, data)


def test_current_observation_never_becomes_a_future_week_prediction():
    now = NOW + timedelta(hours=2, minutes=20)
    data = signals()
    data['air_quality']['observations'] = observation_snapshot(301, now)
    # Full forecast coverage for all seven days, normal throughout.
    data['weather']['forecast'][0]['endTime'] = (NOW + timedelta(days=8)).isoformat()
    data['air_quality']['forecast'] = [{'observed_at': (NOW + timedelta(hours=i)).isoformat(), 'us_aqi': 40} for i in range(192)]
    days, contexts = week_contexts(LOCATION, data, now)
    assert days[0]['posture'] == 'CLOSED'
    assert all(day['posture'] == 'NORMAL' for day in days[1:])
    assert all(not briefing_context(ctx)['observed_air_quality'][0]['used_for_policy'] for ctx in list(contexts.values())[1:])


def test_normal_observation_does_not_override_forecast_and_stale_reading_cannot_trigger():
    now = NOW + timedelta(hours=2, minutes=20)
    data = signals(aqi=301)
    data['air_quality']['observations'] = observation_snapshot(30, now)
    assert policy_context(LOCATION, data, now)['posture'] == 'CLOSED'
    data = signals()
    data['air_quality']['observations'] = observation_snapshot(301, now)
    assert policy_context(LOCATION, data, now + timedelta(hours=4))['posture'] == 'NORMAL'


def test_observation_can_justify_current_disruption_without_forecast_but_not_all_clear():
    now = NOW + timedelta(hours=2, minutes=20)
    data = {'weather': {'available': False}, 'air_quality': {
        'available': True, 'forecast_available': False, 'observations': observation_snapshot(175, now)}}
    context = policy_context(LOCATION, data, now)
    assert context['posture'] == 'TELEWORK' and context['warnings']
    data['air_quality']['observations'] = observation_snapshot(40, now)
    with pytest.raises(ValueError, match='INSUFFICIENT_DATA'):
        policy_context(LOCATION, data, now)


def test_real_tool_parses_airnow_array_alongside_forecast_object(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'synthetic-key')
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    observed = row(dateObserved=now.date().isoformat(), hourObserved=now.strftime('%H:00'), localTimeZone='UTC')
    requests = []
    def handler(request):
        requests.append(request)
        payload = [observed] if request.url.host == 'www.airnowapi.org' else {
            'hourly': {'time': [int(now.timestamp())], 'us_aqi': [40]}}
        return httpx.Response(200, json=payload)
    original = httpx.Client
    monkeypatch.setattr(tools.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    result = tools.get_air_quality(41.8, -87.6)
    assert len(requests) == 2
    assert result['forecast'][0]['us_aqi'] == 40
    assert result['observations']['points'][0]['us_aqi'] == 175


def test_observed_window_selects_only_overlapping_on_site_shifts():
    now = NOW + timedelta(hours=2, minutes=20)  # Monday 09:20 CDT
    data = signals()
    data['air_quality']['observations'] = observation_snapshot(175, now)
    context = policy_context(LOCATION, data, now)
    base = {'location_id': 1, 'is_manager': False, 'is_essential': False,
            'is_teleworking': False, '_invalid_shift': False}
    roster = [base | {'emp_id': 1, 'shift_id': 1, 'weekly_hours': {'mon': ['08:00', '10:00']}},
              base | {'emp_id': 2, 'shift_id': 2, 'weekly_hours': {'mon': ['10:20', '18:00']}},
              base | {'emp_id': 3, 'shift_id': 1, 'weekly_hours': {'mon': ['08:00', '10:00']}, 'is_teleworking': True}]
    selected = filter_roster(roster, LOCATION | {'id': 1}, context['affected_start'], context['affected_end'],
                             context['posture'], impact_windows=context['impact_windows'])
    assert {row['emp_id'] for row in selected if row['included']} == {1}
    assert selected[1]['reason'] == 'not_scheduled' and selected[2]['reason'] == 'already_remote'
