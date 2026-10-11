import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from seed import seed


@pytest.fixture(autouse=True)
def isolate_airnow_key(monkeypatch):
    """Tests opt into a synthetic key; never call AirNow using local credentials."""
    monkeypatch.delenv("AIRNOW_API_KEY", raising=False)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "facility.db")
    seed(path)
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("DEMO_LOGIN", "true")
    monkeypatch.delenv("MANAGER_PASSWORD", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with TestClient(create_app(db_path)) as test_client:
        yield test_client


@pytest.fixture
def headers(client):
    response = client.post("/api/login", json={"emp_id": 1001})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def decision_id(db_path):
    """A clearly synthetic snapshot tests history reads, not evaluation behavior."""
    from app.db import connect

    db = connect(db_path)
    try:
        with db:
            cursor = db.execute(
                """INSERT INTO decisions
                   (location_id, evaluated_at, posture, reason, confidence,
                    evidence_json, threshold_json, alternatives_json, recipients_json,
                    draft_subject, draft_body)
                   VALUES (1, '2026-10-10T12:00:00+00:00', 'NORMAL', 'TEST ONLY', 0.5,
                           '[]', '[]', '{"uncertainty":"Test fixture"}', '[]', 'Test', 'Test draft')"""
            )
            return cursor.lastrowid
    finally:
        db.close()
