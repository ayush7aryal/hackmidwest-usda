import json
from contextlib import closing

import pytest

from app.db import connect
from test_evaluator import evaluation_sources


@pytest.fixture
def evaluated(client, headers, evaluation_sources):
    result = client.post("/api/locations/1/evaluate", headers=headers)
    assert result.status_code == 200 and result.json()["state"] == "COMPLETED"
    decision_id = result.json()["decision_id"]
    detail = client.get(f"/api/decisions/{decision_id}", headers=headers).json()
    return detail


def payload(detail, **changes):
    return {"status": "APPROVED", "posture": detail["posture"], "reason": detail["reason"],
            "draft_subject": "Manager edited subject", "draft_body": "Manager edited message",
            "essential_confirmed": True} | changes


def test_complete_evaluate_preview_edit_approve_workflow(client, headers, evaluated):
    url = f"/api/decisions/{evaluated['id']}"
    assert evaluated["manager_status"] == "PENDING"
    assert evaluated["draft_body"].startswith("DRAFT")
    assert "America/Chicago" in evaluated["draft_body"]
    preview = client.post(url + "/recipients", headers=headers, json={"scope": "all_staff"})
    assert preview.status_code == 200
    assert len([row for row in preview.json() if row["included"]]) == 6
    result = client.post(url + "/review", headers=headers, json=payload(evaluated, scope="all_staff"))
    assert result.status_code == 200
    saved = result.json()
    assert saved["manager_status"] == "APPROVED" and saved["reviewed_by"] == 1001
    assert saved["draft_subject"] == "Manager edited subject"
    assert saved["draft_body"] == "Manager edited message"
    assert saved["sent_at"] is None and saved["delivery"] is None
    assert saved["alternatives"]["original_recommendation"]["reason"] == evaluated["reason"]
    assert saved["alternatives"]["original_draft"]["body"].startswith("DRAFT")
    assert client.post(url + "/send", headers=headers, json={}).status_code == 503


def test_essential_acknowledgment_and_foreign_ids(client, headers, evaluated):
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url + "/review", headers=headers, json=payload(evaluated, essential_confirmed=False)).status_code == 422
    assert client.get(url, headers=headers).json()["manager_status"] == "PENDING"
    assert client.post(url + "/recipients", headers=headers, json={"include_ids": [2101]}).status_code == 422
    assert client.post(url + "/review", headers=headers, json=payload(evaluated, exclude_ids=[2101])).status_code == 422


def test_preview_reloads_staff_and_can_prepare_posture_override(client, headers, evaluated, db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET is_teleworking = 1 WHERE emp_id = 1101")
    rows = client.post(f"/api/decisions/{evaluated['id']}/recipients", headers=headers, json={}).json()
    assert next(row for row in rows if row["emp_id"] == 1101)["reason"] == "already_remote"
    rows = client.post(f"/api/decisions/{evaluated['id']}/recipients", headers=headers, json={"posture": "NORMAL"}).json()
    assert any(row["included"] for row in rows)  # Explicit return-to-normal override informs affected on-site staff.
    assert not next(row for row in rows if row['emp_id'] == 1101)['included']


@pytest.mark.parametrize("posture", ["NORMAL", "DELAYED_OPENING", "EARLY_DISMISSAL", "TELEWORK", "CLOSED"])
def test_manager_controls_final_action_and_original_agent_advice_is_preserved(client, headers, evaluated, posture):
    url = f"/api/decisions/{evaluated['id']}"
    saved = client.post(url + "/review", headers=headers, json=payload(evaluated, posture=posture)).json()
    assert saved["manager_status"] == "APPROVED" and saved["posture"] == posture
    assert saved["reviewed_by"] == 1001
    assert saved["alternatives"]["original_recommendation"]["posture"] == evaluated["posture"]
    assert saved["sent_at"] is None and saved["manager_alert_sent_at"] is None


def test_reviews_preserve_edit_history_and_reopen_approval(client, headers, evaluated):
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url + "/review", headers=headers, json=payload(evaluated)).status_code == 200
    result = client.post(url + "/review", headers=headers, json=payload(evaluated, status="PENDING")).json()
    assert result["manager_status"] == "PENDING"
    assert len(result["alternatives"]["reviews"]) == 2
    assert result["draft_body"].startswith("DRAFT")
    assert client.post(url + "/review", headers=headers, json=payload(evaluated, status="REJECTED")).json()["manager_status"] == "REJECTED"


def test_configuration_changes_invalidate_approval(client, headers, evaluated):
    assert client.patch("/api/locations/1", headers=headers, json={"latitude": 40.0}).status_code == 200
    assert client.get("/api/locations/1", headers=headers).json()["last_checked_at"] is None
    result = client.post(f"/api/decisions/{evaluated['id']}/review", headers=headers, json=payload(evaluated))
    assert result.status_code == 409


def test_expired_decision_cannot_be_approved(client, headers, evaluated, db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE decisions SET affected_start = '2020-01-01T08:00:00Z', affected_end = '2020-01-01T12:00:00Z' WHERE id = ?", (evaluated["id"],))
    result = client.post(f"/api/decisions/{evaluated['id']}/review", headers=headers, json=payload(evaluated))
    assert result.status_code == 409


def test_new_routes_enforce_role_and_ownership(client, headers, evaluated):
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url + "/recipients", json={}).status_code == 401
    assert client.post(url + "/review", json=payload(evaluated)).status_code == 401
    assert client.post("/api/locations/1/evaluate").status_code == 401
    assert client.post("/api/locations/2/evaluate", headers=headers).status_code == 404
    login = client.post("/api/login", json={"emp_id": 2001}).json()
    other = {"Authorization": f"Bearer {login['access_token']}"}
    assert client.post(url + "/recipients", headers=other, json={}).status_code == 404
    assert client.post(url + "/review", headers=other, json=payload(evaluated)).status_code == 404


@pytest.mark.parametrize('prefix', [
    'DRAFT — pending manager approval; this is not a finalized instruction.',
    'APPROVED — reviewed by manager 1001.',
    'REJECTED — reviewed by manager 1001.',
])
def test_approved_employee_message_removes_review_labels_and_legacy_draft_note(client, headers, evaluated, prefix):
    text = ('Facility: Test office\nRecommended action: TELEWORK\n'
            'Your shift overlaps this affected window. This is a draft; your manager decides the final action.\n'
            'Essential personnel: contact your manager.\nManager contact: Alex Morgan\n'
            'Keep the draft schedule spreadsheet for reference.')
    result = client.post(f"/api/decisions/{evaluated['id']}/review", headers=headers,
                        json=payload(evaluated, draft_body=prefix + '\n\n' + text))
    assert result.status_code == 200
    saved = result.json()
    assert saved['manager_status'] == 'APPROVED' and saved['reviewed_by'] == 1001
    assert saved['draft_body'].startswith('Facility: Test office')
    assert 'reviewed by manager' not in saved['draft_body']
    assert 'This is a draft;' not in saved['draft_body']
    assert 'Essential personnel: contact your manager.' in saved['draft_body']
    assert 'Keep the draft schedule spreadsheet' in saved['draft_body']
    assert saved['alternatives']['original_draft'] == evaluated['alternatives']['original_draft']
    assert saved['sent_at'] is None


def test_existing_approved_messages_are_clean_on_read_without_rewriting_audit(client, headers, evaluated, db_path):
    legacy = 'APPROVED — reviewed by manager 1001.\n\nFacility: Test\nWork remotely. This is a draft; your manager decides the final action.'
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE decisions SET manager_status='APPROVED', reviewed_by=1001, draft_body=? WHERE id=?", (legacy, evaluated['id']))
    saved = client.get(f"/api/decisions/{evaluated['id']}", headers=headers).json()
    assert saved['draft_body'] == 'Facility: Test\nWork remotely.'
    assert saved['alternatives']['original_draft'] == evaluated['alternatives']['original_draft']
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT draft_body FROM decisions WHERE id=?', (evaluated['id'],)).fetchone()[0] == legacy
