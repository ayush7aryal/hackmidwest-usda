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
    for key,value in {'EMAIL_ENABLED':'true','EMAIL_PROVIDER':'smtp','SMTP_HOST':'smtp.test.invalid','SMTP_PORT':'587',
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


def test_same_operation_keeps_unnotified_affected_staff_selected(client, headers, db_path, evaluated, provider):
    second=evaluator.evaluate(db_path,1,1001)
    detail=client.get(f"/api/decisions/{second['decision_id']}",headers=headers).json()
    assert detail['alternatives']['notification']=={'required':True,'reason':'disruption'}
    assert {r['emp_id'] for r in detail['recipients'] if r['included']}=={1101,1102,1104,1105}
    url=f"/api/decisions/{detail['id']}"
    same=client.post(url+'/recipients',headers=headers,json={'scope':'all_staff'}).json()
    assert len([r for r in same if r['included']])==7
    rows=client.post(url+'/recipients',headers=headers,json={'posture':'CLOSED'}).json()
    assert {r['emp_id'] for r in rows if r['included']}=={1101,1102,1104,1105}
    approve(client,headers,detail)
    assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'
    third=evaluator.evaluate(db_path,1,1001)
    rows=client.get(f"/api/decisions/{third['decision_id']}",headers=headers).json()['recipients']
    assert not any(r['included'] for r in rows)
    assert {r['emp_id'] for r in rows if r['reason']=='already_notified'}=={1101,1102,1104,1105}


def test_legacy_unchanged_snapshot_recovers_on_preview_and_approval(client, headers, db_path, evaluated, provider):
    with closing(connect(db_path)) as db, db:
        alt=evaluated['alternatives'] | {'notification':{'required':False,'reason':'unchanged_operation'}}
        rows=[r | {'included':False,'reason':'unchanged_operation' if r['included'] else r['reason'],
                   'requires_manager_review':False} for r in evaluated['recipients']]
        db.execute('UPDATE decisions SET alternatives_json=?,recipients_json=? WHERE id=?',
                   (json.dumps(alt),json.dumps(rows),evaluated['id']))
    url=f"/api/decisions/{evaluated['id']}"
    assert not any(r['included'] for r in client.get(url,headers=headers).json()['recipients'])
    rows=client.post(url+'/recipients',headers=headers,json={'scope':'affected'}).json()
    assert {r['emp_id'] for r in rows if r['included']}=={1101,1102,1104,1105}
    saved=approve(client,headers,evaluated)
    assert saved['alternatives']['notification']=={'required':True,'reason':'disruption'}
    assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'


@pytest.mark.parametrize('prior_status,blocked', [('UNKNOWN',True),('IN_PROGRESS',True),('FAILED',False)])
def test_repeat_evaluation_respects_uncertain_submissions_but_allows_definite_failure(
        client, headers, db_path, evaluated, provider, prior_status, blocked):
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE decisions SET delivery_json=? WHERE id=?',
                   (json.dumps({'status':prior_status,'intended_employee_ids':[1101],
                                'requested_at':'2026-10-12T12:00:00Z'}), evaluated['id']))
    second=evaluator.evaluate(db_path,1,1001)
    url=f"/api/decisions/{second['decision_id']}"
    rows=client.post(url+'/recipients',headers=headers,json={}).json()
    row=next(r for r in rows if r['emp_id']==1101)
    assert row['included'] is (not blocked)
    assert row['reason']==('delivery_uncertain' if blocked else 'scheduled_on_site')
    assert {r['emp_id'] for r in rows if r['included']}==({1102,1104,1105} if blocked else {1101,1102,1104,1105})


def test_previously_notified_subset_does_not_hide_other_affected_staff(client, headers, db_path, evaluated, provider):
    approve(client,headers,evaluated,exclude_ids=[1102,1104,1105])
    assert client.post(f"/api/decisions/{evaluated['id']}/send",headers=headers).json()['status']=='ACCEPTED'
    second=evaluator.evaluate(db_path,1,1001)
    detail=client.get(f"/api/decisions/{second['decision_id']}",headers=headers).json()
    assert {r['emp_id'] for r in detail['recipients'] if r['included']}=={1102,1104,1105}
    assert next(r for r in detail['recipients'] if r['emp_id']==1101)['reason']=='already_notified'


def test_normal_has_no_default_audience_but_manager_can_select_everyone(client, headers, evaluated, db_path, provider):
    with closing(connect(db_path)) as db, db:
        alt=json.loads(db.execute('SELECT alternatives_json FROM decisions WHERE id=?',(evaluated['id'],)).fetchone()[0])
        alt['original_recommendation']['posture']='NORMAL'
        db.execute("UPDATE decisions SET posture='NORMAL',alternatives_json=? WHERE id=?",(json.dumps(alt),evaluated['id']))
    detail=client.get(f"/api/decisions/{evaluated['id']}",headers=headers).json()
    url=f"/api/decisions/{detail['id']}"
    rows=client.post(url+'/recipients',headers=headers,json={}).json()
    assert not any(r['included'] for r in rows)
    saved=approve(client,headers,detail,scope='all_staff')
    assert sum(r['included'] for r in saved['recipients'])==7
    assert saved['alternatives']['notification']=={'required':True,'reason':'manager_override'}
    assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'


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


def test_disruption_eligibility_does_not_depend_on_last_evaluation():
    assert notification_rule('TELEWORK')['required']
    assert not notification_rule('NORMAL')['required']
    assert notification_rule('NORMAL',override=True)['required']


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


def test_confirmed_resend_preserves_delivery_history_and_guards_each_submission(client, headers, evaluated, provider):
    url=f"/api/decisions/{evaluated['id']}"
    approve(client,headers,evaluated)
    first=client.post(url+'/send',headers=headers).json()
    assert first['status']=='ACCEPTED'
    for attempt in range(2):
        assert client.post(url+'/review',headers=headers,json=payload(evaluated)).status_code==409
        saved=approve(client,headers,evaluated,resend_confirmed=True,draft_body=f'Updated demo notice {attempt}')
        assert len(saved['alternatives']['delivery_history'])==attempt+1
        assert saved['alternatives']['delivery_history'][0]['delivery']==first
        assert saved['alternatives']['delivery_history'][0]['draft_body']=='Manager edited message'
        assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'
        assert client.post(url+'/send',headers=headers).status_code==409
    assert len(provider)==3
    assert len({str(call[1]['Message-ID']) for call in provider})==3


def test_manual_staff_selection_overrides_normal_remote_and_unscheduled_defaults(client, headers, evaluated, db_path, provider):
    with closing(connect(db_path)) as db, db:
        alt=evaluated['alternatives']
        alt['original_recommendation']['posture']='NORMAL'
        db.execute("UPDATE decisions SET posture='NORMAL',alternatives_json=? WHERE id=?",(json.dumps(alt),evaluated['id']))
    detail=client.get(f"/api/decisions/{evaluated['id']}",headers=headers).json()
    saved=approve(client,headers,detail,include_ids=[1103,1106])
    assert {r['emp_id'] for r in saved['recipients'] if r['included']}=={1103,1106}
    assert client.post(f"/api/decisions/{detail['id']}/send",headers=headers).json()['status']=='ACCEPTED'


def test_manual_previously_notified_selection_requires_confirmation(client, headers, evaluated, db_path, provider):
    approve(client,headers,evaluated)
    client.post(f"/api/decisions/{evaluated['id']}/send",headers=headers)
    result=evaluator.evaluate(db_path,1,1001)
    url=f"/api/decisions/{result['decision_id']}"
    detail=client.get(url,headers=headers).json()
    rows=client.post(url+'/recipients',headers=headers,json={'include_ids':[1101]}).json()
    selected=next(r for r in rows if r['emp_id']==1101)
    assert selected['included'] and selected['previous_delivery']=='already_notified'
    assert client.post(url+'/review',headers=headers,json=payload(detail,include_ids=[1101])).status_code==409
    approve(client,headers,detail,include_ids=[1101],resend_confirmed=True)
    assert client.post(url+'/send',headers=headers).json()['status']=='ACCEPTED'


def test_archived_success_still_prevents_automatic_duplicate_after_failed_resend(client, headers, evaluated, db_path, provider, monkeypatch):
    approve(client,headers,evaluated)
    url=f"/api/decisions/{evaluated['id']}"
    client.post(url+'/send',headers=headers)
    approve(client,headers,evaluated,resend_confirmed=True)
    def failed(*args):
        raise email_delivery.SMTPSetupError('test failure')
    monkeypatch.setattr(email_delivery,'submit_smtp',failed)
    assert client.post(url+'/send',headers=headers).json()['status']=='FAILED'
    result=evaluator.evaluate(db_path,1,1001)
    rows=client.get(f"/api/decisions/{result['decision_id']}",headers=headers).json()['recipients']
    assert not any(r['included'] for r in rows)
    assert {r['emp_id'] for r in rows if r['reason']=='already_notified'}=={1101,1102,1104,1105}


@pytest.mark.parametrize('status',['IN_PROGRESS','UNKNOWN'])
def test_resend_confirmation_cannot_reset_pending_delivery(client, headers, evaluated, db_path, provider, status):
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE decisions SET delivery_json=? WHERE id=?',
                   (json.dumps({'status':status,'intended_employee_ids':[1101]}),evaluated['id']))
    url=f"/api/decisions/{evaluated['id']}"
    assert client.post(url+'/review',headers=headers,json=payload(evaluated,resend_confirmed=True)).status_code==409
    result=evaluator.evaluate(db_path,1,1001)
    other=f"/api/decisions/{result['decision_id']}"
    detail=client.get(other,headers=headers).json()
    assert client.post(other+'/review',headers=headers,json=payload(detail,include_ids=[1101],resend_confirmed=True)).status_code==409
    assert not provider


def test_confirmed_resend_still_rechecks_new_uncertain_submission_before_dispatch(client, headers, evaluated, db_path, provider):
    approve(client,headers,evaluated,include_ids=[1101],resend_confirmed=True)
    second=evaluator.evaluate(db_path,1,1001)
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE decisions SET delivery_json=? WHERE id=?',
                   (json.dumps({'status':'UNKNOWN','intended_employee_ids':[1101]}),second['decision_id']))
    result=client.post(f"/api/decisions/{evaluated['id']}/send",headers=headers)
    assert result.status_code==409
    assert not provider
