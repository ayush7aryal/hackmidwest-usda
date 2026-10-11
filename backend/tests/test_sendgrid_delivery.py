import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from python_http_client.client import Client
from python_http_client.exceptions import HTTPError

from app import email_delivery
from app.db import connect
from test_email_delivery import approve
from test_evaluator import evaluation_sources
from test_review import evaluated


@pytest.fixture
def sendgrid_provider(monkeypatch):
    for key, value in {'EMAIL_ENABLED': 'true', 'EMAIL_PROVIDER': 'sendgrid',
                       'SENDGRID_API_KEY': 'synthetic-test-key', 'EMAIL_FROM': 'sender@example.com',
                       'DEMO_EMAIL_TO': 'owner@gmail.com', 'SMTP_HOST': '', 'SMTP_PASSWORD': ''}.items():
        monkeypatch.setenv(key, value)
    state = {'calls': [], 'failure': None, 'status': 202, 'activity': []}

    def request(client, opener, request, timeout=None):
        state['calls'].append({'url': request.full_url, 'method': request.get_method(),
                               'headers': request.headers, 'body': json.loads(request.data) if request.data else None, 'timeout': timeout})
        if state['failure']:
            raise state['failure']
        return SimpleNamespace(getcode=lambda: 200 if request.get_method() == 'GET' else state['status'],
                               read=lambda: json.dumps({'messages': state['activity']}).encode() if request.get_method() == 'GET' else b'',
                               info=lambda: {'X-Message-Id': 'synthetic-provider-id'})

    def no_smtp(*args):
        raise AssertionError('SendGrid must not fall back to SMTP')

    monkeypatch.setattr(Client, '_make_request', request)
    monkeypatch.setattr(email_delivery, 'submit_smtp', no_smtp)
    return state


def test_sendgrid_sdk_uses_https_only_after_approval(client, headers, evaluated, sendgrid_provider):
    url = f"/api/decisions/{evaluated['id']}"
    assert client.post(url+'/send', headers=headers).status_code == 409
    approve(client, headers, evaluated)
    assert not sendgrid_provider['calls']
    result = client.post(url+'/send', headers=headers)
    assert result.status_code == 200
    delivery = result.json()
    assert delivery['status'] == 'ACCEPTED' and delivery['provider'] == 'sendgrid'
    assert delivery['http_status'] == 202 and delivery['provider_message_id'] == 'synthetic-provider-id'
    assert delivery['accepted_employee_ids'] and delivery['accepted_addresses'] == ['owner@gmail.com']
    request = sendgrid_provider['calls'][0]
    assert request['url'] == 'https://api.sendgrid.com/v3/mail/send'
    assert request['method'] == 'POST' and request['timeout'] == 20
    assert request['headers']['Authorization'] == 'Bearer synthetic-test-key'
    assert request['body']['personalizations'] == [{'to': [{'email': 'owner@gmail.com'}]}]
    assert 'DEMO DELIVERY' in request['body']['content'][0]['value']
    saved = client.get(url, headers=headers).json()
    assert saved['sent_at'] and saved['delivery'] == delivery
    assert 'synthetic-test-key' not in json.dumps(saved['delivery'])
    assert client.post(url+'/send', headers=headers).status_code == 409
    assert len(sendgrid_provider['calls']) == 1


def test_each_sendgrid_recipient_has_a_private_envelope(client, headers, evaluated, db_path, monkeypatch, sendgrid_provider):
    monkeypatch.setenv('DEMO_EMAIL_TO', '')
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET email='owner+' || emp_id || '@gmail.com' WHERE is_manager=0")
    saved = approve(client, headers, evaluated)
    selected = {r['email'] for r in saved['recipients'] if r['included']}
    result = client.post(f"/api/decisions/{evaluated['id']}/send", headers=headers).json()
    assert result['status'] == 'ACCEPTED' and set(result['accepted_addresses']) == selected
    envelopes = sendgrid_provider['calls'][0]['body']['personalizations']
    assert len(envelopes) == len(selected)
    assert all(set(p) == {'to'} and len(p['to']) == 1 for p in envelopes)
    assert {p['to'][0]['email'] for p in envelopes} == selected


@pytest.mark.parametrize('code,expected,retry', [(400, 'FAILED', True), (401, 'FAILED', True),
    (403, 'FAILED', True), (429, 'FAILED', True), (408, 'UNKNOWN', False), (500, 'UNKNOWN', False)])
def test_sendgrid_rejections_and_uncertain_results(client, headers, evaluated, sendgrid_provider, code, expected, retry):
    approve(client, headers, evaluated)
    sendgrid_provider['failure'] = HTTPError(code, 'Synthetic failure', b'private provider response', {})
    url = f"/api/decisions/{evaluated['id']}/send"
    result = client.post(url, headers=headers).json()
    assert result['status'] == expected and result['http_status'] == code
    assert not result['accepted_addresses']
    assert 'private provider response' not in json.dumps(result)
    assert client.get(f"/api/decisions/{evaluated['id']}", headers=headers).json()['sent_at'] is None
    assert len(sendgrid_provider['calls']) == 1
    sendgrid_provider['failure'] = None
    response = client.post(url, headers=headers)
    assert response.status_code == (200 if retry else 409)
    if retry:
        assert response.json()['status'] == 'ACCEPTED'


def test_sendgrid_timeout_cannot_trigger_a_duplicate_retry(client, headers, evaluated, sendgrid_provider):
    approve(client, headers, evaluated)
    sendgrid_provider['failure'] = TimeoutError('Synthetic timeout')
    url = f"/api/decisions/{evaluated['id']}/send"
    assert client.post(url, headers=headers).json()['status'] == 'UNKNOWN'
    assert client.post(url, headers=headers).status_code == 409
    assert len(sendgrid_provider['calls']) == 1


def test_sendgrid_requires_202_acceptance(client, headers, evaluated, sendgrid_provider):
    approve(client, headers, evaluated)
    sendgrid_provider['status'] = 200
    result = client.post(f"/api/decisions/{evaluated['id']}/send", headers=headers).json()
    assert result['status'] == 'UNKNOWN' and not result['accepted_addresses']


@pytest.mark.parametrize('key,value', [('SENDGRID_API_KEY', ''), ('SENDGRID_API_KEY', 'bad key'),
    ('EMAIL_FROM', ''), ('EMAIL_ENABLED', 'false'), ('EMAIL_PROVIDER', 'unsupported')])
def test_sendgrid_configuration_guards(client, headers, evaluated, sendgrid_provider, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    status = client.get('/api/email-status', headers=headers).json()
    assert status['ready'] is False and 'synthetic-test-key' not in json.dumps(status)
    assert client.post(f"/api/decisions/{evaluated['id']}/send", headers=headers).status_code == 503
    assert not sendgrid_provider['calls']


def test_sendgrid_readiness_does_not_contact_provider_or_expose_key(client, headers, sendgrid_provider):
    status = client.get('/api/email-status', headers=headers).json()
    assert status['ready'] is True and status['provider'] == 'sendgrid'
    assert 'synthetic-test-key' not in json.dumps(status) and not sendgrid_provider['calls']
