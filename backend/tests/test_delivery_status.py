import json
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

from python_http_client.exceptions import HTTPError

from app import delivery_status
from app.db import connect
from test_email_delivery import approve
from test_evaluator import evaluation_sources
from test_review import evaluated
from test_sendgrid_delivery import sendgrid_provider


def send(client, headers, evaluated):
    approve(client, headers, evaluated)
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url + '/send', headers=headers).json()['status'] == 'ACCEPTED'
    return url


def test_activity_matches_exact_submission_and_recipient_and_never_resends(client, headers, evaluated, sendgrid_provider):
    url = send(client, headers, evaluated)
    sendgrid_provider['activity'] = [
        {'msg_id': 'synthetic-provider-id.recvd-1.0', 'to_email': 'owner@gmail.com',
         'status': 'delivered', 'last_event_time': '2026-10-11T01:00:00Z'},
        {'msg_id': 'synthetic-provider-id-other.recvd-1.0', 'to_email': 'owner@gmail.com', 'status': 'delivered'},
        {'msg_id': 'synthetic-provider-id.recvd-1.1', 'to_email': 'unrelated@example.com', 'status': 'delivered'}]
    result = client.post(url + '/delivery-status', headers=headers).json()
    assert result['status'] == 'ACCEPTED'  # Transport status is not reinterpreted as inbox delivery.
    assert result['activity']['available'] is True
    assert result['activity']['receipts'] == [{'to_email': 'owner@gmail.com', 'status': 'delivered', 'last_event_time': '2026-10-11T01:00:00Z'}]
    call = sendgrid_provider['calls'][-1]
    assert call['method'] == 'GET' and call['timeout'] == 10
    assert parse_qs(urlsplit(call['url']).query)['query'] == ['msg_id LIKE "synthetic-provider-id%"']
    assert client.get(url, headers=headers).json()['delivery'] == result
    assert client.post(url + '/delivery-status', headers=headers).json() == result  # Short cache.
    assert len(sendgrid_provider['calls']) == 2


def test_activity_unavailable_does_not_claim_delivery_or_resend(client, headers, evaluated, sendgrid_provider):
    url = send(client, headers, evaluated)
    sendgrid_provider['failure'] = HTTPError(403, 'Denied', b'private', {})
    result = client.post(url + '/delivery-status', headers=headers).json()
    assert result['activity']['available'] is False and not result['activity']['receipts']
    assert 'private' not in json.dumps(result)
    assert result['status'] == 'ACCEPTED' and len(sendgrid_provider['calls']) == 2


def test_activity_is_owned_and_requires_an_accepted_submission(client, headers, evaluated, sendgrid_provider):
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url + '/delivery-status').status_code == 401
    assert client.post(url + '/delivery-status', headers=headers).status_code == 409
    foreign = client.post('/api/login', json={'emp_id': 2001}).json()['access_token']
    assert client.post(url + '/delivery-status', headers={'Authorization': f'Bearer {foreign}'}).status_code == 404
    assert not sendgrid_provider['calls']


def test_activity_does_not_overwrite_concurrent_resend(client, headers, evaluated, db_path, sendgrid_provider, monkeypatch):
    url = send(client, headers, evaluated)
    def activity(delivery):
        with closing(connect(db_path)) as db, db:
            db.execute('UPDATE decisions SET delivery_json=? WHERE id=?', ('{"status":"IN_PROGRESS"}', evaluated['id']))
        return {'available': True, 'receipts': []}
    monkeypatch.setattr(delivery_status, 'provider_activity', activity)
    assert client.post(url + '/delivery-status', headers=headers).status_code == 409
    assert client.get(url, headers=headers).json()['delivery'] == {'status': 'IN_PROGRESS'}
