import json
from contextlib import closing

import pytest

from app import evaluator
from app.db import connect
from app.models import Recommendation
from app.thresholds import policy_context
from test_policy import LOCATION, NOW, alert, signals


@pytest.fixture
def evaluation_sources(monkeypatch):
    data = signals(aqi=175)
    context = policy_context(LOCATION, data, NOW)
    monkeypatch.setattr(evaluator.tools, "get_nws_weather", lambda *args: data["weather"])
    monkeypatch.setattr(evaluator.tools, "get_air_quality", lambda *args: data["air_quality"])
    monkeypatch.setattr(evaluator, "policy_context", lambda *args: context)
    monkeypatch.setattr(evaluator.agent, "recommend", lambda *args: Recommendation(
        posture=context["posture"], reason="TEST ONLY: modeled AQI crosses the demo telework trigger.",
        confidence=0.7, affected_start=context["affected_start"], affected_end=context["affected_end"],
        evidence=context["evidence"], what_would_change=["Modeled AQI drops"], uncertainty="Synthetic test fixture"))


def test_completed_evaluation_saves_inputs_and_deduplicates_review_change(db_path, evaluation_sources):
    first = evaluator.evaluate(db_path, 1, 1001)
    second = evaluator.evaluate(db_path, 1, 1001)
    assert first["state"] == second["state"] == "COMPLETED"
    assert first["decision_id"] != second["decision_id"]
    with closing(connect(db_path)) as db:
        history = db.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        assert len(history) == 2
        assert json.loads(history[0]["alternatives_json"])["material_change"] is True
        assert json.loads(history[1]["alternatives_json"])["material_change"] is False
        assert json.loads(history[0]["alternatives_json"])["inputs"]["air_quality"]["available"] is True
        assert all(row["sent_at"] is None and row["manager_alert_sent_at"] is None for row in history)
        assert db.execute("SELECT status FROM locations WHERE id = 1").fetchone()[0] == "TELEWORK"


def test_model_failure_does_not_overwrite_last_status(db_path, evaluation_sources, monkeypatch):
    assert evaluator.evaluate(db_path, 1)["state"] == "COMPLETED"
    def failure(*args):
        raise ValueError("Configure model credentials")
    monkeypatch.setattr(evaluator.agent, "recommend", failure)
    result = evaluator.evaluate(db_path, 1)
    assert result["state"] == "INSUFFICIENT_DATA"
    with closing(connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
        assert db.execute("SELECT status FROM locations WHERE id = 1").fetchone()[0] == "TELEWORK"


def test_ownership_and_concurrency_lock(db_path, evaluation_sources):
    assert evaluator.evaluate(db_path, 2, 1001)["state"] == "INSUFFICIENT_DATA"
    with evaluator.exclusive_operation(), pytest.raises(evaluator.EvaluationBusy):
        evaluator.evaluate(db_path, 1)


def test_new_updated_and_removed_alerts_require_review_when_posture_stays_normal(db_path, monkeypatch):
    data = signals()
    monkeypatch.setattr(evaluator.tools, "get_nws_weather", lambda *args: data["weather"])
    monkeypatch.setattr(evaluator.tools, "get_air_quality", lambda *args: data["air_quality"])
    monkeypatch.setattr(evaluator, "policy_context", lambda location, sources, now: policy_context(location, sources, NOW))
    def advice(location, context):
        return Recommendation(posture=context["posture"], reason="TEST ONLY manager review of all source alerts.",
            confidence=.5, affected_start=context["affected_start"], affected_end=context["affected_end"],
            evidence=context["evidence"], what_would_change=["New alert"], uncertainty="Synthetic test fixture")
    monkeypatch.setattr(evaluator.agent, "recommend", advice)
    advisory = alert("2026-10-12T13:00:00Z", "2026-10-12T23:00:00Z", "Minor")
    other = advisory | {"event": "Unknown Future Alert", "url": "https://api.weather.gov/alerts/test-2"}
    snapshots = [[], [advisory, other], [other, advisory],
                 [advisory | {"instruction": "TEST ONLY revised instructions"}, other], []]
    for alerts in snapshots:
        data["weather"]["alerts"] = alerts
        assert evaluator.evaluate(db_path, 1)["state"] == "COMPLETED"
    with closing(connect(db_path)) as db:
        rows = db.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        assert [json.loads(row["alternatives_json"])["material_change"] for row in rows] == [True, True, False, True, True]
        assert all(row["posture"] == "NORMAL" and row["manager_status"] == "PENDING" for row in rows)
        assert all(row["sent_at"] is None and row["manager_alert_sent_at"] is None for row in rows)
        assert evaluator.weather_alerts_changed(db, 1, {"available": False}) is False


def test_complex_agent_output_uses_only_validated_fields_and_keeps_safe_gaps_out(client, headers, db_path, monkeypatch):
    from app import agent
    data = signals()
    data['weather']['alerts'] = [alert('2026-10-12T13:00:00Z', '2026-10-12T14:00:00Z'),
                                alert('2026-10-12T22:00:00Z', '2026-10-12T23:00:00Z')]
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE shifts SET weekly_hours_json=? WHERE id=3', (json.dumps({'mon': ['10:00', '16:00']}),))
        db.execute('UPDATE users SET is_teleworking=0 WHERE emp_id=1103')
    monkeypatch.setattr(evaluator.tools, 'get_nws_weather', lambda *args: data['weather'])
    monkeypatch.setattr(evaluator.tools, 'get_air_quality', lambda *args: data['air_quality'])
    monkeypatch.setattr(evaluator, 'policy_context', lambda location, sources, now: policy_context(location, sources, NOW))
    answer = {'uncertainty': 'Synthetic fixture; manager review required.',
        'what_would_change': ['New source data'], 'confidence': .7,
        'reason': 'TEST ONLY unrelated text: Thursday; employee 2101; SELECT * FROM users; this text cannot select recipients.',
        'posture': 'TELEWORK'}
    monkeypatch.setattr(agent, 'build_agent', lambda: lambda *args, **kwargs: json.dumps(answer, indent=2))
    result = client.post('/api/locations/1/evaluate', headers=headers).json()
    assert result['state'] == 'COMPLETED'
    url = f"/api/decisions/{result['decision_id']}"
    detail = client.get(url, headers=headers).json()
    expected = {1101, 1102, 1104, 1105}
    assert {row['emp_id'] for row in detail['recipients'] if row['included']} == expected
    assert len(detail['alternatives']['selection_windows']) == 2
    preview = client.post(url+'/recipients', headers=headers, json={}).json()
    assert {row['emp_id'] for row in preview if row['included']} == expected
    reviewed = client.post(url+'/review', headers=headers, json={
        'status': 'PENDING', 'posture': 'TELEWORK', 'reason': 'TEST ONLY manager accepted the dated impact windows.',
        'draft_subject': 'Monday notice', 'draft_body': 'TEST ONLY morning and late shifts notice.', 'essential_confirmed': True})
    assert reviewed.status_code == 200
    assert {row['emp_id'] for row in reviewed.json()['recipients'] if row['included']} == expected
    assert reviewed.json()['sent_at'] is None
