"""Manager-first workflow, using the real SDK with a fake HTTPS transport."""

import json
from contextlib import closing

import pytest
from python_http_client.exceptions import HTTPError

from app import evaluator, manager_alerts
from app.db import connect
from app.models import Recommendation
from app.scheduler import evaluate_all
from app.thresholds import policy_context
from test_evaluator import evaluation_sources
from test_policy import LOCATION, NOW, alert, signals
from test_review import payload
from test_sendgrid_delivery import sendgrid_provider


@pytest.fixture
def manager_provider(db_path, monkeypatch, sendgrid_provider):
    monkeypatch.setenv('MANAGER_ALERTS_ENABLED', 'true')
    monkeypatch.setenv('APP_BASE_URL', 'https://dashboard.test')
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET email='manager@gmail.com' WHERE emp_id=1001")
    return sendgrid_provider


def saved(db_path, decision_id):
    with closing(connect(db_path)) as db:
        row = dict(db.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone())
        row['manager_alert'] = json.loads(row['alternatives_json'])['manager_alert']
        return row


def test_manager_first_then_explicit_employee_send(client, headers, db_path, evaluation_sources, manager_provider):
    result = client.post('/api/locations/1/evaluate', headers=headers).json()
    assert result['state'] == 'COMPLETED'
    url = f"/api/decisions/{result['decision_id']}"
    row = saved(db_path, result['decision_id'])
    assert row['manager_alert_sent_at'] and not row['sent_at'] and not row['delivery_json']
    assert row['manager_status'] == 'PENDING'
    assert row['manager_alert']['status'] == 'ACCEPTED'
    request = manager_provider['calls'][0]['body']
    assert request['personalizations'] == [{'to': [{'email': 'manager@gmail.com'}]}]
    text = request['content'][0]['value']
    assert 'NORMAL to TELEWORK' in text and 'Manager ID (login): 1001' in text
    assert 'Alex Morgan' in text and 'manager@gmail.com' in text
    assert f"https://dashboard.test/#decision/{row['id']}" in text
    assert 'CDT' in text and row['reason'] in text
    assert 'No employee emails have been sent' in text
    assert client.post(url+'/send', headers=headers).status_code == 409
    detail = client.get(url, headers=headers).json()
    assert client.post(url+'/review', headers=headers, json=payload(detail)).status_code == 200
    assert len(manager_provider['calls']) == 1  # Approval itself never sends.
    assert client.post(url+'/send', headers=headers).json()['status'] == 'ACCEPTED'
    assert len(manager_provider['calls']) == 2
    assert client.post(url+'/send', headers=headers).status_code == 409


def test_repeat_checks_and_repeat_claim_do_not_email_manager_again(db_path, evaluation_sources, manager_provider):
    first = evaluator.evaluate(db_path, 1)
    second = evaluator.evaluate(db_path, 1)
    assert saved(db_path, second['decision_id'])['manager_alert']['status'] == 'SUPPRESSED'
    with closing(connect(db_path)) as db:
        assert manager_alerts.notify_manager(db, first['decision_id'])['status'] == 'ACCEPTED'
    assert len(manager_provider['calls']) == 1


def test_transition_requires_previous_normal_and_can_recur_after_recovery(db_path, evaluation_sources, manager_provider):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE locations SET status='CLOSED' WHERE id=1")
    result = evaluator.evaluate(db_path, 1)
    assert saved(db_path, result['decision_id'])['manager_alert']['status'] == 'SUPPRESSED'
    assert not manager_provider['calls']  # CLOSED -> TELEWORK is not NORMAL -> disruption.
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE locations SET status='NORMAL' WHERE id=1")
    assert evaluator.evaluate(db_path, 1)['state'] == 'COMPLETED'
    assert len(manager_provider['calls']) == 1


def test_disabled_manager_alert_still_has_preview_without_provider_call(
        db_path, monkeypatch, evaluation_sources, manager_provider):
    monkeypatch.setenv('MANAGER_ALERTS_ENABLED', 'false')
    result = evaluator.evaluate(db_path, 1)
    row = saved(db_path, result['decision_id'])
    assert row['manager_alert']['status'] == 'DISABLED'
    assert row['manager_alert']['template']['to'] == ['manager@gmail.com']
    assert not manager_provider['calls']


def test_all_assigned_managers_receive_private_envelopes_and_claim_is_committed(
        db_path, evaluation_sources, manager_provider, monkeypatch):
    from python_http_client.client import Client
    original = Client._make_request
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET is_manager=1, email='second-manager@gmail.com' WHERE emp_id=1101")
    def request(*args, **kwargs):
        with closing(connect(db_path)) as db, db:
            row = db.execute('SELECT * FROM decisions ORDER BY id DESC LIMIT 1').fetchone()
            assert json.loads(row['alternatives_json'])['manager_alert']['status'] == 'IN_PROGRESS'
            db.execute('UPDATE users SET name=name WHERE emp_id=1001')  # No write lock held during HTTPS.
        return original(*args, **kwargs)
    monkeypatch.setattr(Client, '_make_request', request)
    result = evaluator.evaluate(db_path, 1)
    assert saved(db_path, result['decision_id'])['manager_alert']['status'] == 'ACCEPTED'
    envelopes = manager_provider['calls'][0]['body']['personalizations']
    assert len(envelopes) == 2 and all(len(item['to']) == 1 for item in envelopes)
    assert {item['to'][0]['email'] for item in envelopes} == {'manager@gmail.com', 'second-manager@gmail.com'}


@pytest.mark.parametrize('posture,data', [
    ('NORMAL', signals()), ('TELEWORK', signals(aqi=175)), ('CLOSED', signals(aqi=310)),
    ('DELAYED_OPENING', signals(alert=alert('2026-10-12T13:00:00Z', '2026-10-12T16:00:00Z'))),
    ('EARLY_DISMISSAL', signals(alert=alert('2026-10-12T20:00:00Z', '2026-10-12T23:00:00Z'))),
])
def test_each_disruption_posture_triggers_but_normal_does_not(db_path, monkeypatch, manager_provider, posture, data):
    context = policy_context(LOCATION, data, NOW)
    monkeypatch.setattr(evaluator.tools, 'fetch_signals', lambda *args: data)
    monkeypatch.setattr(evaluator, 'policy_context', lambda *args: context)
    monkeypatch.setattr(evaluator.agent, 'recommend', lambda *args: Recommendation(
        posture=context['posture'], reason='TEST ONLY: deterministic input threshold crossed.', confidence=.7,
        affected_start=context['affected_start'], affected_end=context['affected_end'], evidence=context['evidence'],
        what_would_change=['Updated sources'], uncertainty='Synthetic test'))
    result = evaluator.evaluate(db_path, 1)
    assert result['state'] == 'COMPLETED'
    row = saved(db_path, result['decision_id'])
    assert row['posture'] == posture
    assert len(manager_provider['calls']) == (0 if posture == 'NORMAL' else 1)


@pytest.mark.parametrize('failure,expected', [(HTTPError(403, 'Rejected', b'private', {}), 'FAILED'),
                                            (TimeoutError('private'), 'UNKNOWN')])
def test_manager_email_failure_does_not_lose_evaluation_or_notify_employees(
        db_path, evaluation_sources, manager_provider, failure, expected):
    manager_provider['failure'] = failure
    result = evaluator.evaluate(db_path, 1)
    assert result['state'] == 'COMPLETED' and 'needs attention' in result['message']
    row = saved(db_path, result['decision_id'])
    assert row['posture'] == 'TELEWORK' and row['manager_status'] == 'PENDING'
    assert not row['sent_at'] and not row['manager_alert_sent_at'] and not row['delivery_json']
    assert row['manager_alert']['status'] == expected
    assert 'private' not in json.dumps(row['manager_alert'])
    manager_provider['failure'] = None
    with closing(connect(db_path)) as db:
        manager_alerts.notify_manager(db, row['id'])
        assert db.execute('SELECT status FROM locations WHERE id=1').fetchone()[0] == 'TELEWORK'
    assert len(manager_provider['calls']) == 1


def test_no_assigned_manager_is_recorded_without_losing_recommendation(db_path, evaluation_sources, manager_provider):
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE users SET is_manager=0 WHERE location_id=1')
    result = evaluator.evaluate(db_path, 1)
    assert result['state'] == 'COMPLETED'
    assert saved(db_path, result['decision_id'])['manager_alert']['status'] == 'FAILED'
    assert not manager_provider['calls']


def test_scheduled_check_uses_assigned_managers_and_never_employee_send(db_path, evaluation_sources, manager_provider):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET email='other-manager@gmail.com' WHERE emp_id=2001")
    evaluate_all(db_path)
    destinations = [call['body']['personalizations'][0]['to'][0]['email'] for call in manager_provider['calls']]
    assert destinations == ['manager@gmail.com', 'other-manager@gmail.com']
    evaluate_all(db_path)
    assert len(manager_provider['calls']) == 2
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM decisions WHERE sent_at IS NOT NULL').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM decisions WHERE manager_alert_sent_at IS NOT NULL').fetchone()[0] == 2


def test_manager_identity_is_authenticated_and_current(client, headers, db_path):
    assert client.get('/api/me').status_code == 401
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET email='updated-manager@gmail.com' WHERE emp_id=1001")
    assert client.get('/api/me', headers=headers).json() == {
        'emp_id': 1001, 'name': 'Alex Morgan', 'email': 'updated-manager@gmail.com', 'location_id': 1}
