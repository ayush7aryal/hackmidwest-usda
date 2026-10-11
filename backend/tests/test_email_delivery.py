import json
import smtplib
from contextlib import closing

import pytest

from app import email_delivery, evaluator
from app.db import connect
from app.notification_rules import notification_rule
from test_review import evaluated, payload
from test_evaluator import evaluation_sources

REAL_SUBMIT = email_delivery.submit_smtp


@pytest.fixture
def provider(monkeypatch):
    for key,value in {'EMAIL_ENABLED':'true','SMTP_HOST':'smtp.test.invalid','SMTP_PORT':'587',
                      'SMTP_USERNAME':'test-user','SMTP_PASSWORD':'test-secret','EMAIL_FROM':'sender@example.com',
                      'DEMO_EMAIL_TO':'owner@gmail.com'}.items():
        monkeypatch.setenv(key,value)
    calls=[]
    monkeypatch.setattr(email_delivery, 'submit_smtp', lambda *args: calls.append(args) or {})
    return calls


def approve(client, headers, detail, **changes):
    r=client.post(f"/api/decisions/{detail['id']}/review",headers=headers,json=payload(detail,**changes))
    assert r.status_code==200, r.json()
    return r.json()


def test_approval_then_explicit_send_is_idempotent(client, headers, evaluated, provider):
    url=f"/api/decisions/{evaluated['id']}"
    assert client.post(url+'/send',headers=headers).status_code==409
    approve(client,headers,evaluated)
    assert provider==[]
    sent=client.post(url+'/send',headers=headers)
    assert sent.status_code==200 and sent.json()['status']=='ACCEPTED'
    assert len(provider)==1 and provider[0][2]==['owner@gmail.com']
    assert 'synthetic employee addresses were not contacted' in provider[0][1].get_content()
    assert not provider[0][1].get('Bcc')
    saved=client.get(url,headers=headers).json()
    assert saved['sent_at'] and saved['delivery']['accepted_employee_ids']
    assert client.post(url+'/send',headers=headers).status_code==409
    assert client.post(url+'/review',headers=headers,json=payload(evaluated)).status_code==409
    assert len(provider)==1


def test_same_operation_unchecked_but_override_reselects_affected_staff(client, headers, db_path, evaluated, provider):
    second=evaluator.evaluate(db_path,1,1001)
    detail=client.get(f"/api/decisions/{second['decision_id']}",headers=headers).json()
    assert detail['alternatives']['notification']=={'required':False,'reason':'unchanged_operation'}
    assert not any(r['included'] for r in detail['recipients'])
    url=f"/api/decisions/{detail['id']}"
    same=client.post(url+'/recipients',headers=headers,json={'scope':'all_staff'}).json()
    assert not any(r['included'] for r in same)
    rows=client.post(url+'/recipients',headers=headers,json={'posture':'CLOSED'}).json()
    assert {r['emp_id'] for r in rows if r['included']}=={1101,1102,1104,1105}
    approve(client,headers,detail,posture='CLOSED')
    assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'


def test_normal_automatic_action_has_no_audience_even_with_all_staff_scope(client, headers, evaluated, db_path, provider):
    with closing(connect(db_path)) as db, db:
        alt=json.loads(db.execute('SELECT alternatives_json FROM decisions WHERE id=?',(evaluated['id'],)).fetchone()[0])
        alt['original_recommendation']['posture']='NORMAL'
        db.execute("UPDATE decisions SET posture='NORMAL',alternatives_json=? WHERE id=?",(json.dumps(alt),evaluated['id']))
    detail=client.get(f"/api/decisions/{evaluated['id']}",headers=headers).json()
    saved=approve(client,headers,detail,scope='all_staff')
    assert not any(r['included'] for r in saved['recipients'])
    assert client.post(f"/api/decisions/{detail['id']}/send",headers=headers).status_code==409
    assert not provider


def test_explicit_return_to_normal_notifies_scheduled_staff(client, headers, evaluated, provider):
    saved=approve(client,headers,evaluated,posture='NORMAL')
    assert saved['alternatives']['notification']['reason']=='manager_override'
    assert any(r['included'] for r in saved['recipients'])
    assert client.post(f"/api/decisions/{saved['id']}/send",headers=headers).json()['status']=='ACCEPTED'


def test_no_cross_decision_repeat_for_same_action_and_window(client, headers, evaluated, db_path, provider):
    approve(client,headers,evaluated,posture='CLOSED')
    assert client.post(f"/api/decisions/{evaluated['id']}/send",headers=headers).json()['status']=='ACCEPTED'
    second=evaluator.evaluate(db_path,1,1001)
    detail=client.get(f"/api/decisions/{second['decision_id']}",headers=headers).json()
    rows=client.post(f"/api/decisions/{detail['id']}/recipients",headers=headers,json={'posture':'CLOSED'}).json()
    assert not any(r['included'] for r in rows)
    assert any(r['reason']=='already_notified' for r in rows)
    # A new action remains eligible even after the previous one was submitted.
    rows=client.post(f"/api/decisions/{detail['id']}/recipients",headers=headers,json={'posture':'NORMAL'}).json()
    assert any(r['included'] for r in rows)


def test_fresh_staff_check_refuses_stale_approved_audience(client, headers, evaluated, db_path, provider):
    approve(client,headers,evaluated)
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE users SET is_teleworking=1 WHERE emp_id=1101')
    result=client.post(f"/api/decisions/{evaluated['id']}/send",headers=headers)
    assert result.status_code==409 and 'selection changed' in result.json()['detail']
    assert not provider


@pytest.mark.parametrize('failure,status,retry',[(TimeoutError('test'),'UNKNOWN',False),
    (email_delivery.SMTPSetupError('test'),'FAILED',True),
    (smtplib.SMTPRecipientsRefused({'owner@gmail.com':(550,b'test')}),'FAILED',True)])
def test_failed_and_uncertain_delivery_have_safe_retry_rules(client, headers, evaluated, provider, monkeypatch, failure, status, retry):
    approve(client,headers,evaluated)
    def fail(*args):
        raise failure
    monkeypatch.setattr(email_delivery,'submit_smtp',fail)
    url=f"/api/decisions/{evaluated['id']}/send"
    assert client.post(url,headers=headers).json()['status']==status
    monkeypatch.setattr(email_delivery,'submit_smtp',lambda *args: {})
    response=client.post(url,headers=headers)
    assert response.status_code==(200 if retry else 409)


def test_partial_acceptance_is_recorded_and_never_blindly_retried(client, headers, evaluated, db_path, provider, monkeypatch):
    monkeypatch.setenv('DEMO_EMAIL_TO','')
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE users SET email='staff' || emp_id || '@demo.invalid' WHERE is_manager=0")
    approve(client,headers,evaluated)
    monkeypatch.setattr(email_delivery,'submit_smtp',lambda config,message,targets:{targets[0]:(550,b'test')})
    url=f"/api/decisions/{evaluated['id']}/send"
    result=client.post(url,headers=headers)
    assert result.status_code==200 and result.json()['status']=='PARTIAL'
    assert result.json()['accepted_addresses'] and result.json()['refused_addresses']
    assert len(result.json()['accepted_employee_ids'])==3
    assert client.post(url,headers=headers).status_code==409


def test_send_ownership_setup_and_header_guards(client, headers, evaluated, provider, monkeypatch):
    url=f"/api/decisions/{evaluated['id']}"
    assert client.post(url+'/send').status_code==401
    other=client.post('/api/login',json={'emp_id':2001}).json()
    assert client.post(url+'/send',headers={'Authorization':'Bearer '+other['access_token']}).status_code==404
    assert client.post(url+'/review',headers=headers,json=payload(evaluated,draft_subject='Bad\nBcc: outsider@test.com')).status_code==422
    monkeypatch.setenv('SMTP_PASSWORD','')
    assert client.get('/api/email-status',headers=headers).json()['ready'] is False
    assert client.post(url+'/send',headers=headers).status_code==503
    assert not provider


def test_new_dates_and_extended_windows_are_not_silently_suppressed():
    snapshot={'status':'TELEWORK','last_checked_at':'2026-10-12T12:00:00Z',
              'affected_start':'2026-10-12T13:00:00Z','affected_end':'2026-10-12T23:00:00Z'}
    assert not notification_rule(snapshot,'TELEWORK','2026-10-12T13:00:00Z','2026-10-12T23:00:00Z')['required']
    assert notification_rule(snapshot,'TELEWORK','2026-10-13T13:00:00Z','2026-10-13T23:00:00Z')['required']
    assert notification_rule(snapshot,'TELEWORK','2026-10-12T13:00:00Z','2026-10-13T00:00:00Z')['required']


def test_real_smtp_adapter_requires_tls_and_does_not_retry(monkeypatch, provider):
    events=[]
    class SMTP:
        def __init__(self,*args,**kwargs): events.append('connect')
        def ehlo(self): events.append('ehlo')
        def starttls(self,context): assert context.check_hostname; events.append('tls')
        def login(self,*args): events.append('login')
        def send_message(self,*args,**kwargs): events.append('submit'); return {}
        def close(self): events.append('close')
    monkeypatch.setattr(email_delivery.smtplib,'SMTP',SMTP)
    config=email_delivery.smtp_settings()
    message,targets=email_delivery.delivery_message(config,{'id':1,'draft_subject':'Test','draft_body':'Test'},[{'email':'staff@example.com'}])
    assert REAL_SUBMIT(config,message,targets)=={}
    assert events==['connect','ehlo','tls','ehlo','login','submit','close']
