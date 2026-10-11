from datetime import UTC, datetime, timedelta

import pytest

from app.agent import build_agent, validate_recommendation
from app.models import AgentAdvice, Recommendation
from app.thresholds import operating_window, policy_context

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
LOCATION = {"name": "Test Office", "latitude": 41.8, "longitude": -87.6,
            "timezone": "America/Chicago", "open_time": "08:00", "close_time": "18:00"}


def signals(aqi=40, alert=None):
    """Explicit test fixtures; timestamps cover the test office's complete day."""
    return {"weather": {"available": True, "source_urls": ["https://api.weather.gov/test"],
        "fetched_at": NOW.isoformat(), "alerts": [alert] if alert else [], "forecast": [
            {"startTime": NOW.isoformat(), "endTime": (NOW + timedelta(hours=24)).isoformat(), "shortForecast": "Test"}]},
        "air_quality": {"source": "Open-Meteo / CAMS", "available": True,
            "source_urls": ["https://air-quality-api.open-meteo.com/test"], "forecast": [
                {"observed_at": (NOW + timedelta(hours=index)).isoformat(), "us_aqi": aqi} for index in range(24)]}}


def alert(start, end, severity="Severe"):
    return {"event": "TEST ALERT", "severity": severity, "urgency": "Immediate", "certainty": "Observed",
            "onset": start, "ends": end, "sent": NOW.isoformat(), "url": "https://api.weather.gov/alerts/test"}


@pytest.mark.parametrize("data,posture", [
    (signals(), "NORMAL"), (signals(aqi=151), "TELEWORK"), (signals(aqi=301), "CLOSED"),
    (signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T16:00:00Z")), "DELAYED_OPENING"),
    (signals(alert=alert("2026-10-12T20:00:00Z", "2026-10-12T23:00:00Z")), "EARLY_DISMISSAL"),
    (signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z")), "TELEWORK"),
    (signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T16:00:00Z", "Extreme")), "CLOSED"),
    (signals(alert=alert("2026-10-13T13:00:00Z", "2026-10-13T16:00:00Z")), "NORMAL"),
])
def test_all_five_postures_are_grounded_in_actual_inputs(data, posture):
    result = policy_context(LOCATION, data, NOW)
    assert result["posture"] == posture
    assert result["evidence"]
    assert all(row["policy_version"] == "demo-office-v4" for row in result["thresholds"])


def test_missing_sources_dont_invent_an_all_clear():
    data = signals()
    data["weather"] = {"available": False}
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        policy_context(LOCATION, data, NOW)
    data["air_quality"]["forecast"][2]["us_aqi"] = 200
    result = policy_context(LOCATION, data, NOW)
    assert result["posture"] == "TELEWORK" and result["confidence_cap"] == 0.5
    data = signals()
    data["air_quality"] = {"available": False}
    assert policy_context(LOCATION, data, NOW)["warnings"]


def test_panama_advisories_are_preserved_and_require_review_without_forcing_closure():
    data = signals()
    data["weather"]["alerts"] = [
        alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Moderate") | {
            "event": "Rip Current Statement", "description": "TEST ONLY dangerous rip currents",
            "instruction": "TEST ONLY avoid the surf", "url": "https://api.weather.gov/alerts/rip-test"},
        alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Minor") | {
            "event": "High Surf Advisory", "url": "https://api.weather.gov/alerts/surf-test"},
    ]
    result = policy_context(LOCATION, data, NOW)
    assert result["posture"] == "NORMAL"
    alert_evidence = [entry for entry in result["evidence"] if entry["value"].get("event")]
    assert {entry["value"]["event"] for entry in alert_evidence} == {"Rip Current Statement", "High Surf Advisory"}
    assert result["weather_alerts"][0]["instruction"] == "TEST ONLY avoid the surf"
    assert all("below the Severe/Extreme" in entry["demo_policy_assessment"] for entry in result["weather_alerts"])
    assert set(alert_evidence[0]["value"]) == {"event", "severity", "urgency", "certainty"}
    assert len(result["warnings"]) == 2 and result["confidence_cap"] == 0.5
    assert len([row for row in result["thresholds"] if row["rule"] == "NWS alert assessment"]) == 2


def test_outside_window_alert_is_visible_with_explicit_non_overlap_reason():
    result = policy_context(LOCATION, signals(alert=alert("2026-10-13T13:00:00Z", "2026-10-13T16:00:00Z")), NOW)
    assert result["posture"] == "NORMAL"
    assert "Outside the evaluated office window" in result["thresholds"][0]["margin"]
    assert any(entry["value"].get("event") == "TEST ALERT" for entry in result["evidence"])
    assert not result["warnings"]


@pytest.mark.parametrize("begins,ends", [("invalid", "2026-10-12T23:00:00Z"), (None, None),
    ("2026-10-12T23:00:00Z", "2026-10-12T13:00:00Z")])
def test_advisory_with_unknown_timing_cannot_be_silently_dropped(begins, ends):
    advisory = alert(begins, ends, "Moderate") | {"effective": begins, "expires": ends}
    result = policy_context(LOCATION, signals(alert=advisory), NOW)
    assert result["warnings"] and result["confidence_cap"] == 0.5
    assert "timing is incomplete" in result["thresholds"][0]["margin"]


@pytest.mark.parametrize("event,severity", [
    ("Tornado Warning", "Extreme"), ("Flash Flood Warning", "Severe"),
    ("Hurricane Watch", "Severe"), ("Winter Storm Warning", "Severe"),
    ("Red Flag Warning", "Severe"), ("Heat Advisory", "Moderate"),
    ("Dense Fog Advisory", "Minor"), ("Air Quality Alert", "Unknown"),
    ("Special Weather Statement", None), ("New Unrecognized Event", "Unknown"),
])
def test_alert_handling_has_no_event_type_or_severity_allowlist(event, severity):
    data = signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", severity) | {"event": event})
    context = policy_context(LOCATION, data, NOW)
    assert context["weather_alerts"][0]["event"] == event
    assert any(entry["value"].get("event") == event for entry in context["evidence"])
    assert context["thresholds"][0]["actual"].startswith(event)


def test_unknown_status_requires_review_instead_of_being_classed_as_a_test():
    data = signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Extreme") | {"status": None})
    context = policy_context(LOCATION, data, NOW)
    assert context["posture"] == "NORMAL" and "status is unknown" in context["warnings"][0]


def test_forecast_outage_retains_alert_evidence_and_alert_outage_cannot_support_all_clear():
    data = signals(alert=alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Extreme"))
    data["weather"].update(available=False, forecast_available=False, alerts_available=True)
    context = policy_context(LOCATION, data, NOW)
    assert context["posture"] == "CLOSED" and context["weather_alerts"] and context["warnings"]
    data = signals()
    data["weather"].update(available=False, forecast_available=True, alerts_available=False)
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        policy_context(LOCATION, data, NOW)


@pytest.mark.parametrize("change", [{"status": "Test"}, {"messageType": "Cancel"}])
def test_test_and_canceled_alerts_do_not_trigger_office_closure(change):
    severe = alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Extreme") | change
    result = policy_context(LOCATION, signals(alert=severe), NOW)
    assert result["posture"] == "NORMAL"
    assert "Test, exercise or canceled" in result["thresholds"][0]["margin"]


def test_stale_forecast_cannot_support_normal():
    data = signals()
    data["weather"]["forecast"][0]["endTime"] = NOW.isoformat()
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        policy_context(LOCATION, data, NOW)


def test_internal_forecast_gaps_cannot_support_all_clear():
    data = signals()
    data["weather"]["forecast"] = [
        {"startTime": NOW.isoformat(), "endTime": "2026-10-12T14:00:00Z"},
        {"startTime": "2026-10-12T16:00:00Z", "endTime": "2026-10-13T00:00:00Z"},
    ]
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        policy_context(LOCATION, data, NOW)


def test_next_opening_uses_actual_local_date_and_dst():
    start, end = operating_window(LOCATION, datetime(2026, 10, 13, 1, tzinfo=UTC))
    assert start == datetime(2026, 10, 13, 13, tzinfo=UTC)
    assert end == datetime(2026, 10, 13, 23, tzinfo=UTC)
    start, _ = operating_window(LOCATION, datetime(2026, 11, 2, 12, tzinfo=UTC))
    assert start.hour == 14


def test_agent_has_exactly_two_tools_and_requires_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Configure"):
        build_agent()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_ID", "test-model")
    agent = build_agent()
    assert {entry["name"] for entry in agent.tool_registry.get_all_tool_specs()} == {"get_nws_weather", "get_air_quality"}


def test_model_cannot_fabricate_citations_posture_or_window():
    context = policy_context(LOCATION, signals(), NOW)
    result = Recommendation(posture=context["posture"], reason="Test explanation of actual forecast risk.",
        confidence=0.99, affected_start=context["affected_start"], affected_end=context["affected_end"],
        evidence=context["evidence"], what_would_change=["A new alert"], uncertainty="Test only")
    assert validate_recommendation(result, context).confidence == 0.8
    result.posture = "CLOSED"
    with pytest.raises(ValueError, match="posture"):
        validate_recommendation(result, context)
    result.posture = "NORMAL"
    result.evidence[0].url = "https://invented.example"
    with pytest.raises(ValueError, match="evidence"):
        validate_recommendation(result, context)


@pytest.mark.parametrize("base_url", [None, "", "https://provider.example/v1"])
@pytest.mark.parametrize("stream", [True, False])
def test_real_strands_openai_adapter_returns_validated_json(monkeypatch, base_url, stream):
    """Exercise the installed SDK with a mocked provider HTTP response, never a real key."""
    import json
    import httpx2
    from app import agent
    advisory = alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Moderate") | {
        "event": "Rip Current Statement", "instruction": "TEST ONLY avoid the water"}
    context = policy_context(LOCATION, signals(alert=advisory), NOW)
    # Formatting and precision must survive without asking the model to copy them.
    context["evidence"][0]["value"]["forecast"][0]["shortForecast"] = "TEST ONLY\n* Risk: rain & surf"
    context["affected_start"] = "2026-10-12T13:00:00.123456+00:00"
    answer = Recommendation(posture=context["posture"], reason="TEST ONLY: no qualifying weather or AQI trigger.",
        confidence=0.7, affected_start=context["affected_start"], affected_end=context["affected_end"],
        evidence=context["evidence"], what_would_change=["New alert"], uncertainty="Synthetic HTTP response")
    calls = []
    def provider(request):
        assert str(request.url) == (base_url or "https://api.openai.com/v1") + "/chat/completions"
        body = json.loads(request.content)
        calls.append(body)
        assert body["max_tokens"] == 4096
        assert body["response_format"]["type"] == "json_schema"
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema == AgentAdvice.model_json_schema()
        assert not {"evidence", "affected_start", "affected_end"} & set(schema["properties"])
        assert "TEST ONLY avoid the water" in json.dumps(body["messages"])
        assert {entry["function"]["name"] for entry in body["tools"]} == {"get_nws_weather", "get_air_quality"}
        content = answer.model_dump_json(exclude={"evidence", "affected_start", "affected_end"})
        assert body["stream"] is stream
        if stream:
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            events = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, text=events)
        return httpx2.Response(200, json={"id": "test-response", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "message": {"role": "assistant",
                "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    original_model = agent.OpenAIModel
    def model(**kwargs):
        assert kwargs["client_args"]["max_retries"] == 0
        kwargs["client_args"]["http_client"] = httpx2.AsyncClient(transport=httpx2.MockTransport(provider))
        return original_model(**kwargs, stream=stream)
    monkeypatch.setattr(agent, "OpenAIModel", model)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_ID", "test-model")
    if base_url is None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    answer.confidence = context["confidence_cap"]
    assert agent.recommend(LOCATION, context) == answer
    assert len(calls) == 1


def test_real_sdk_throttling_has_no_nested_six_attempt_retry_loop(monkeypatch):
    import httpx2
    from app import agent
    calls = []
    def provider(request):
        calls.append(request)
        return httpx2.Response(429, json={"error": {"message": "TEST ONLY rate limit", "type": "rate_limit_exceeded"}})
    original_model = agent.OpenAIModel
    def model(**kwargs):
        # Disable SDK HTTP retries in this test to observe the Strands retry layer alone.
        kwargs["client_args"]["max_retries"] = 0
        kwargs["client_args"]["http_client"] = httpx2.AsyncClient(transport=httpx2.MockTransport(provider))
        return original_model(**kwargs)
    monkeypatch.setattr(agent, "OpenAIModel", model)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("MODEL_ID", "test-model")
    with pytest.raises(ValueError, match="quota/rate limit"):
        agent.recommend(LOCATION, policy_context(LOCATION, signals(), NOW))
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ['not json', '{}', '{"posture":"CLOSED"}'])
def test_bad_model_advice_is_rejected_without_guessing_missing_fields(monkeypatch, invalid):
    from pydantic import ValidationError
    from app import agent
    monkeypatch.setattr(agent, "build_agent", lambda: lambda *args, **kwargs: invalid)
    with pytest.raises(ValidationError):
        agent.recommend(LOCATION, policy_context(LOCATION, signals(), NOW))


def test_model_cannot_replace_backend_evidence_or_impact_times(monkeypatch):
    from pydantic import ValidationError
    from app import agent
    context = policy_context(LOCATION, signals(), NOW)
    answer = {"posture": "NORMAL", "reason": "TEST ONLY source-based explanation for review.",
              "confidence": .5, "what_would_change": ["New alert"], "uncertainty": "Synthetic test",
              "evidence": [{"url": "https://invented.example"}], "affected_start": "2020-01-01T00:00:00Z"}
    import json
    monkeypatch.setattr(agent, "build_agent", lambda: lambda *args, **kwargs: json.dumps(answer))
    with pytest.raises(ValidationError):
        agent.recommend(LOCATION, context)


@pytest.mark.parametrize("status,expected", [(401, "authentication"), (403, "authentication"),
    (429, "quota/rate limit"), (500, "temporarily unavailable"), (400, "HTTP 400")])
def test_provider_errors_are_specific_and_do_not_expose_response_bodies(status, expected, caplog):
    import httpx2
    from openai import APIStatusError
    from app.agent import invoke
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://provider.example/v1/chat/completions"))
    def failed(*args, **kwargs):
        raise APIStatusError("SECRET TEST BODY", response=response, body={"secret": "SECRET TEST BODY"})
    with pytest.raises(ValueError, match=expected) as raised:
        invoke(failed, "TEST ONLY")
    assert "SECRET TEST BODY" not in str(raised.value) and "SECRET TEST BODY" not in caplog.text


@pytest.mark.parametrize("failure,expected", [
    ("timeout", "timed out"), ("connection", "Cannot connect"),
    ("tokens", "incomplete"), ("context", "context limit"), ("quota", "quota/rate limit")])
def test_model_transport_and_limit_failures_are_actionable(failure, expected):
    import httpx2
    from openai import APIConnectionError, APITimeoutError
    from strands.types.exceptions import ContextWindowOverflowException, MaxTokensReachedException, ModelThrottledException
    from app.agent import invoke
    request = httpx2.Request("POST", "https://provider.example/v1/chat/completions")
    errors = {"timeout": APITimeoutError(request=request), "connection": APIConnectionError(request=request),
              "tokens": MaxTokensReachedException("TEST ONLY"), "context": ContextWindowOverflowException("TEST ONLY"),
              "quota": ModelThrottledException("TEST ONLY")}
    def failed(*args, **kwargs):
        raise errors[failure]
    with pytest.raises(ValueError, match=expected):
        invoke(failed, "TEST ONLY")


@pytest.mark.parametrize('value,expected', [('40 mph', 40), ('10 to 45 mph', 45), ('39.5 mph', 39.5),
    ('65 km/h', None), ('gusts 45 mph', None), (None, None), ('calm', None)])
def test_forecast_wind_requires_explicit_sustained_mph(value, expected):
    from app.thresholds import forecast_wind_mph
    assert forecast_wind_mph(value) == expected


def test_compact_briefing_preserves_hazard_changes_gaps_alerts_and_original_evidence():
    import copy
    from app.agent import briefing_context
    data = signals(alert=alert('2026-10-12T13:00:00Z', '2026-10-12T23:00:00Z'))
    context = policy_context(LOCATION, data, NOW)
    context['evidence'][0]['value']['forecast'] = [
        {'startTime': '2026-10-12T13:00:00Z', 'endTime': '2026-10-12T14:00:00Z', 'windSpeed': '10 mph', 'shortForecast': 'Cloudy'},
        {'startTime': '2026-10-12T14:00:00Z', 'endTime': '2026-10-12T15:00:00Z', 'windSpeed': '10 mph', 'shortForecast': 'Cloudy'},
        {'startTime': '2026-10-12T15:00:00Z', 'endTime': '2026-10-12T16:00:00Z', 'windSpeed': '45 mph', 'shortForecast': 'Thunderstorms'},
        {'startTime': '2026-10-12T18:00:00Z', 'endTime': '2026-10-12T19:00:00Z', 'windSpeed': '45 mph', 'shortForecast': 'Thunderstorms'},
    ]
    original = copy.deepcopy(context)
    compact = briefing_context(context)
    assert len(compact['forecast']) == 3
    assert compact['forecast'][0]['endTime'] == '2026-10-12T15:00:00Z'
    assert compact['forecast'][1]['windSpeed'] == '45 mph'
    assert compact['forecast'][2]['startTime'] == '2026-10-12T18:00:00Z'
    assert compact['weather_alerts'] == original['weather_alerts']
    assert compact['impact_windows'] == original['impact_windows']
    assert compact['modeled_air_quality'][0]['us_aqi'] == 40
    assert context == original
    assert 'thresholds' not in compact and 'evidence' not in compact
