from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token
from app.db import connect
from app.main import create_app

FACILITY = {"name": "Boston Office", "latitude": 42.3601, "longitude": -71.0589,
            "timezone": "America/New_York", "open_time": "08:00", "close_time": "17:00"}


def test_health_and_manager_login(client, headers):
    assert client.get("/api/health").json() == {"status": "ok"}
    own = client.get("/api/locations", headers=headers)
    assert own.status_code == 200
    assert [row["id"] for row in own.json()] == [1]
    assert own.json()[0]["status"] == "NORMAL"
    assert own.json()[0]["last_checked_at"] is None
    other_login = client.post("/api/login", json={"emp_id": 2001}).json()
    other = {"Authorization": f"Bearer {other_login['access_token']}"}
    assert client.get("/api/locations", headers=other).json()[0]["id"] == 2


@pytest.mark.parametrize("resource_id", [0, -1, 99999999999999999999999999])
def test_path_ids_cannot_overflow_sqlite(client, headers, resource_id):
    assert client.get(f"/api/locations/{resource_id}", headers=headers).status_code == 422
    assert client.get(f"/api/decisions/{resource_id}", headers=headers).status_code == 422


@pytest.mark.parametrize("emp_id", [1101, 9999])
def test_staff_and_unknown_users_cannot_login(client, emp_id):
    assert client.post("/api/login", json={"emp_id": emp_id}).status_code == 401


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/locations", None),
    ("POST", "/api/locations", FACILITY),
    ("GET", "/api/locations/1", None),
    ("PATCH", "/api/locations/1", {"name": "Unauthorized"}),
    ("GET", "/api/locations/1/employees", None),
    ("GET", "/api/locations/1/decisions", None),
    ("GET", "/api/locations/1/weather-alerts", None),
    ("GET", "/api/decisions/1", None),
])
def test_every_facility_and_decision_route_requires_login(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("suffix", ["", "/employees", "/decisions", "/weather-alerts"])
def test_other_facility_is_hidden(client, headers, suffix):
    assert client.get(f"/api/locations/2{suffix}", headers=headers).status_code == 404
    assert client.patch("/api/locations/2", headers=headers, json={"name": "Other"}).status_code == 404


def test_manager_receives_all_alerts_without_agent_or_decision(client, headers, monkeypatch, db_path):
    calls = []
    alerts = [{"event": "Unknown Future Alert", "instruction": "TEST ONLY manager instructions", "severity": None}]
    def source(latitude, longitude, include_forecast=True):
        calls.append((latitude, longitude, include_forecast))
        return {"alerts_available": True, "alerts": alerts, "fetched_at": "2026-10-10T18:00:00Z",
                "source_urls": ["https://api.weather.gov/alerts/active?point=test"]}
    monkeypatch.setattr("app.main.tools.get_nws_weather", source)
    def no_agent(*args):
        raise AssertionError("Alert display must never depend on an agent call")
    monkeypatch.setattr("app.evaluator.agent.recommend", no_agent)
    result = client.get('/api/locations/1/weather-alerts', headers=headers)
    assert result.status_code == 200 and result.json()["available"] is True
    assert result.json()["alerts"] == alerts
    assert calls == [(41.8781, -87.6298, False)]
    with closing(connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert db.execute("SELECT last_checked_at FROM locations WHERE id=1").fetchone()[0] is None


def test_manager_alert_feed_reports_source_failure(client, headers, monkeypatch):
    monkeypatch.setattr("app.main.tools.get_nws_weather", lambda *args, **kwargs: {
        "alerts_available": False, "alerts": [], "fetched_at": "2026-10-10T18:00:00Z",
        "source_urls": ["https://api.weather.gov/alerts/active?point=test"], "alerts_error": "TEST ONLY source unavailable"})
    result = client.get('/api/locations/1/weather-alerts', headers=headers).json()
    assert result["available"] is False and result["error"] == "TEST ONLY source unavailable"


def test_sessions_reject_tampering_expiry_and_removed_role(client, headers, db_path, monkeypatch):
    assert client.get("/api/locations", headers={"Authorization": "Bearer malformed"}).status_code == 401
    token = headers["Authorization"].replace("1001:", "2001:")
    assert client.get("/api/locations", headers={"Authorization": token}).status_code == 401
    monkeypatch.setattr("app.auth.time.time", lambda: 1)
    expired = issue_token(1001, client.app.state.session_secret)
    monkeypatch.setattr("app.auth.time.time", lambda: 100_000)
    assert client.get("/api/locations", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET is_manager = 0 WHERE emp_id = 1001")
    assert client.get("/api/locations", headers=headers).status_code == 401


@pytest.mark.parametrize("change", [
    {"name": "  "}, {"latitude": 91}, {"longitude": -181}, {"latitude": "NaN"},
    {"timezone": "Not/AZone"}, {"timezone": ""}, {"open_time": "8:00"},
    {"close_time": "24:00"}, {"close_time": "08:00"}, {"close_time": "07:00"},
    {"id": 0}, {"id": True}, {"status": "CLOSED"},
])
def test_create_validates_facility(client, headers, change):
    assert client.post("/api/locations", headers=headers, json=FACILITY | change).status_code == 422


@pytest.mark.parametrize("change", [
    {}, {"name": None}, {"latitude": None}, {"id": None}, {"id": -1},
    {"name": " "}, {"timezone": "Bad/Zone"}, {"close_time": "07:00"},
    {"open_time": "7:00"}, {"latitude": 91}, {"status": "CLOSED"},
])
def test_patch_validates_complete_merged_facility(client, headers, change):
    assert client.patch("/api/locations/1", headers=headers, json=change).status_code == 422


def test_create_reuses_the_same_employee_sheet_without_duplicates(client, headers, db_path):
    original = client.get('/api/locations/1/employees', headers=headers).json()
    response = client.post("/api/locations", headers=headers, json=FACILITY)
    assert response.status_code == 201
    facility = response.json()
    assert facility["name"] == "Boston Office"
    assert facility["status"] == "NORMAL"
    assert client.get("/api/locations", headers=headers).json()[0]["id"] == facility["id"]
    staff = client.get(f"/api/locations/{facility['id']}/employees", headers=headers).json()
    assert len(staff) == 7
    assert any(person["emp_id"] == 1001 for person in staff)
    assert all(person["email"].endswith("@example.com") for person in staff)
    assert staff == [person | {"location_id": facility['id']} for person in original]
    assert client.get("/api/locations/1", headers=headers).status_code == 404
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM users WHERE location_id=1').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 14
        assert db.execute('SELECT COUNT(*) FROM users WHERE location_id=2').fetchone()[0] == 7
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_adding_multiple_demo_locations_keeps_employee_identities_and_flags(client, headers, db_path):
    original = client.get('/api/locations/1/employees', headers=headers).json()
    for name in ('First Demo Location', 'Second Demo Location'):
        facility = client.post('/api/locations', headers=headers, json=FACILITY | {"name": name}).json()
        assert facility['last_checked_at'] is None
        staff = client.get(f"/api/locations/{facility['id']}/employees", headers=headers).json()
        assert staff == [person | {"location_id": facility['id']} for person in original]
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 14
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('posture,expected_ids', [
    ('NORMAL', set()), ('DELAYED_OPENING', {1101, 1102, 1104, 1105}),
    ('EARLY_DISMISSAL', {1102, 1105}), ('TELEWORK', {1101, 1102, 1104, 1105}),
    ('CLOSED', {1101, 1102, 1104, 1105}),
])
def test_new_location_filters_reused_employees_for_every_status(client, headers, monkeypatch, posture, expected_ids):
    """Synthetic sources exercise creation -> evaluation -> dated recipient selection."""
    from app import evaluator
    from app.models import Recommendation
    from app.thresholds import policy_context
    from test_policy import NOW, alert, signals
    data = signals(aqi=175 if posture == 'TELEWORK' else 301 if posture == 'CLOSED' else 40)
    if posture in ('DELAYED_OPENING', 'EARLY_DISMISSAL'):
        begins, ends = ('2026-10-12T12:00:00Z', '2026-10-12T15:00:00Z') if posture == 'DELAYED_OPENING' else ('2026-10-12T20:00:00Z', '2026-10-12T21:00:00Z')
        data['weather']['alerts'] = [alert(begins, ends)]
    monkeypatch.setattr(evaluator.tools, 'get_nws_weather', lambda *args: data['weather'])
    monkeypatch.setattr(evaluator.tools, 'get_air_quality', lambda *args: data['air_quality'])
    monkeypatch.setattr(evaluator, 'policy_context', lambda location, sources, now: policy_context(location, sources, NOW))
    def advice(location, context):
        assert location['timezone'] == 'America/New_York'
        return Recommendation(posture=context['posture'], reason='TEST ONLY: evaluate reused employees at the new location.',
            confidence=.5, affected_start=context['affected_start'], affected_end=context['affected_end'],
            evidence=context['evidence'], what_would_change=['New source data'], uncertainty='Synthetic test fixture')
    monkeypatch.setattr(evaluator.agent, 'recommend', advice)
    facility = client.post('/api/locations', headers=headers, json=FACILITY).json()
    result = client.post(f"/api/locations/{facility['id']}/evaluate", headers=headers).json()
    assert result['state'] == 'COMPLETED'
    decision = client.get(f"/api/decisions/{result['decision_id']}", headers=headers).json()
    assert decision['posture'] == posture and decision['manager_status'] == 'PENDING'
    assert {person['emp_id'] for person in decision['recipients']} == {1001, 1101, 1102, 1103, 1104, 1105, 1106}
    assert {person['emp_id'] for person in decision['recipients'] if person['included']} == expected_ids
    assert all(person['requires_manager_review'] for person in decision['recipients'] if person['included'] and person['is_essential'])
    assert decision['sent_at'] is None and decision['manager_alert_sent_at'] is None


def test_create_accepts_custom_id_and_conflict_rolls_back(client, headers):
    assert client.post("/api/locations", headers=headers, json=FACILITY | {"id": 2}).status_code == 409
    assert client.get("/api/locations", headers=headers).json()[0]["id"] == 1
    assert client.post("/api/locations", headers=headers, json=FACILITY | {"id": 50}).json()["id"] == 50


def test_edit_id_coordinates_and_name_preserves_all_references(client, headers, db_path, decision_id):
    result = client.patch("/api/locations/1", headers=headers,
                          json={"id": 10, "name": "Renamed Office", "latitude": 40.0, "longitude": -90.0})
    assert result.status_code == 200
    assert result.json()["id"] == 10
    assert result.json()["name"] == "Renamed Office"
    assert result.json()["latitude"] == 40.0
    assert result.json()["longitude"] == -90.0
    assert client.get("/api/locations", headers=headers).json()[0]["id"] == 10
    assert client.get("/api/locations/1", headers=headers).status_code == 404
    assert len(client.get("/api/locations/10/employees", headers=headers).json()) == 7
    assert client.get(f"/api/decisions/{decision_id}", headers=headers).json()["location_id"] == 10
    with closing(connect(db_path)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_edit_conflict_is_atomic(client, headers, decision_id):
    assert client.patch("/api/locations/1", headers=headers, json={"id": 2, "name": "Lost edit"}).status_code == 409
    assert client.get("/api/locations/1", headers=headers).json()["name"] == "Chicago Office"
    assert client.get(f"/api/decisions/{decision_id}", headers=headers).json()["location_id"] == 1


def test_staff_read_includes_shift_and_flags(client, headers, db_path):
    staff = client.get("/api/locations/1/employees", headers=headers).json()
    assert len(staff) == 7
    employee = next(row for row in staff if row["emp_id"] == 1102)
    assert employee["is_essential"] is True
    assert employee["shift_name"] == "Weekday late"
    assert employee["weekly_hours"]["mon"] == ["10:00", "18:00"]
    assert employee["weekly_hours"]["sun"] is None
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET shift_id = NULL WHERE emp_id = 1102")
    employee = next(row for row in client.get("/api/locations/1/employees", headers=headers).json() if row["emp_id"] == 1102)
    assert employee["shift_name"] is None
    assert employee["weekly_hours"] is None


def test_decision_history_json_and_ownership(client, headers, decision_id):
    history = client.get("/api/locations/1/decisions?limit=1", headers=headers).json()
    assert [row["id"] for row in history] == [decision_id]
    assert "draft_body" not in history[0]
    detail = client.get(f"/api/decisions/{decision_id}", headers=headers).json()
    assert detail["evidence"] == []
    assert detail["alternatives"]["uncertainty"] == "Test fixture"
    assert detail["manager_status"] == "PENDING"
    assert detail["sent_at"] is None
    assert client.get("/api/locations/1/decisions?offset=1", headers=headers).json() == []
    assert client.get("/api/locations/1/decisions?limit=101", headers=headers).status_code == 422
    assert client.get("/api/decisions/999", headers=headers).status_code == 404
    login = client.post("/api/login", json={"emp_id": 2001}).json()
    assert client.get(f"/api/decisions/{decision_id}", headers={"Authorization": f"Bearer {login['access_token']}"}).status_code == 404


def test_public_login_configuration_and_password(db_path, monkeypatch):
    monkeypatch.setenv("DEMO_LOGIN", "false")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("MANAGER_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="Public login requires"):
        create_app(db_path)
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    monkeypatch.setenv("MANAGER_PASSWORD", "test-password-long")
    with TestClient(create_app(db_path)) as secure:
        assert secure.post("/api/login", json={"emp_id": 1001}).status_code == 401
        assert secure.post("/api/login", json={"emp_id": 1001, "password": "wrong"}).status_code == 401
        assert secure.post("/api/login", json={"emp_id": 1001, "password": "test-password-long"}).status_code == 200


def test_persistence_after_app_restart(client, headers, db_path, monkeypatch):
    assert client.patch("/api/locations/1", headers=headers, json={"name": "Persistent edit"}).status_code == 200
    with TestClient(create_app(db_path)) as restarted:
        login = restarted.post("/api/login", json={"emp_id": 1001}).json()
        auth = {"Authorization": f"Bearer {login['access_token']}"}
        assert restarted.get("/api/locations/1", headers=auth).json()["name"] == "Persistent edit"
