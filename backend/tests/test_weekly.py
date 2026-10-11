"""Synthetic dated hazards test weekly selection; no weather or mail is fabricated live."""

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from app import agent, weekly
from app.db import connect
from app.models import Evidence, Recommendation, WeeklyAdvice
from app.thresholds import policy_context
from test_policy import LOCATION, NOW, alert, signals


def week_signals():
    data = signals()
    data['weather']['forecast'] = [{
        'startTime': (NOW + timedelta(hours=index)).isoformat(),
        'endTime': (NOW + timedelta(hours=index + 1)).isoformat(),
        'windSpeed': '10 mph', 'shortForecast': 'TEST ONLY clear',
    } for index in range(168)]
    data['air_quality']['forecast'] = [
        {'observed_at': (NOW + timedelta(hours=index)).isoformat(), 'us_aqi': 40}
        for index in range(168)]
    data['weather']['alerts'] = [alert('2026-10-14T13:00:00Z', '2026-10-14T23:00:00Z') | {'event': 'TEST ONLY Wednesday storm'}]
    return data


@pytest.fixture
def weekly_sources(monkeypatch):
    data, calls = week_signals(), []
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(weekly, 'datetime', Clock)
    def weather(*args):
        calls.append('weather')
        return data['weather']
    def air(*args):
        calls.append('air')
        return data['air_quality']
    def advice(location, contexts):
        calls.append('agent')
        return {date: Recommendation(posture=context['posture'],
            reason=f'TEST ONLY: dated risk explanation for {date}, manager review required.', confidence=.5,
            affected_start=context['affected_start'], affected_end=context['affected_end'], evidence=context['evidence'],
            what_would_change=['New source data'], uncertainty='Synthetic test fixture') for date, context in contexts.items()}
    monkeypatch.setattr(weekly.tools, 'get_nws_weather', weather)
    monkeypatch.setattr(weekly.tools, 'get_air_quality', air)
    monkeypatch.setattr(weekly.agent, 'recommend_week', advice)
    return data, calls


def evaluate_week(client, headers):
    response = client.post('/api/locations/1/evaluate-week', headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result['state'] == 'COMPLETED', result
    return result


def test_wednesday_storm_creates_two_separate_shift_messages_only(client, headers, db_path, weekly_sources):
    result = evaluate_week(client, headers)
    assert weekly_sources[1] == ['weather', 'air', 'agent']
    assert len(result['days']) == 7
    wednesday = next(day for day in result['days'] if day['date'] == '2026-10-14')
    assert wednesday['posture'] == 'TELEWORK' and len(wednesday['message_groups']) == 2
    assert all(not day['message_groups'] for day in result['days'] if day['date'] != '2026-10-14')
    expected = {1: {1101, 1104}, 2: {1102, 1105}}
    for group in wednesday['message_groups']:
        decision = client.get(f"/api/decisions/{group['decision_id']}", headers=headers).json()
        assert {row['emp_id'] for row in decision['recipients'] if row['included']} == expected[group['shift_id']]
        assert group['recipient_count'] == 2
        assert '2026-10-14' in decision['draft_body'] and group['shift_name'] in decision['draft_subject']
        assert decision['manager_status'] == 'PENDING' and decision['sent_at'] is None
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 8
        assert db.execute('SELECT last_checked_at FROM locations WHERE id=1').fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 4


def test_hazard_starting_at_early_shift_end_only_notifies_late_shift(client, headers, weekly_sources):
    weekly_sources[0]['weather']['alerts'] = [alert('2026-10-14T21:00:00Z', '2026-10-14T23:00:00Z')]
    result = evaluate_week(client, headers)
    groups = next(day['message_groups'] for day in result['days'] if day['date'] == '2026-10-14')
    assert len(groups) == 1 and groups[0]['shift_id'] == 2
    assert groups[0]['posture'] == 'EARLY_DISMISSAL' and groups[0]['recipient_count'] == 2


def test_weekly_counts_use_employee_delivery_instead_of_last_recommendation(client, headers, db_path, weekly_sources):
    first = evaluate_week(client, headers)
    groups = first['days'][2]['message_groups']
    # A daily recommendation alone must not clear the weekly audience.
    with closing(connect(db_path)) as db, db:
        db.execute("""UPDATE locations SET status='TELEWORK', last_checked_at=?,
                   affected_start=?, affected_end=? WHERE id=1""",
                   (NOW.isoformat(), '2026-10-14T13:00:00Z', '2026-10-14T23:00:00Z'))
    second = evaluate_week(client, headers)
    assert [group['recipient_count'] for group in second['days'][2]['message_groups']] == [2, 2]
    # Only the employee actually submitted in this synthetic prior delivery is excluded.
    with closing(connect(db_path)) as db, db:
        db.execute('UPDATE decisions SET sent_at=?, delivery_json=? WHERE id=?',
                   (NOW.isoformat(), json.dumps({'status': 'ACCEPTED', 'accepted_employee_ids': [1101]}),
                    groups[0]['decision_id']))
    third = evaluate_week(client, headers)
    assert [group['recipient_count'] for group in third['days'][2]['message_groups']] == [1, 2]
    for group in third['days'][2]['message_groups']:
        detail = client.get(f"/api/decisions/{group['decision_id']}", headers=headers).json()
        assert group['recipient_count'] == sum(row['included'] for row in detail['recipients'])


def test_disjoint_hazards_do_not_notify_a_shift_working_only_in_the_gap(client, headers, db_path, weekly_sources):
    data = weekly_sources[0]
    data['weather']['alerts'] = [alert('2026-10-14T13:00:00Z', '2026-10-14T14:00:00Z'),
                                alert('2026-10-14T22:00:00Z', '2026-10-14T23:00:00Z')]
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE shifts SET weekly_hours_json = ? WHERE id=3", (json.dumps({'wed': ['10:00', '16:00']}),))
        db.execute('UPDATE users SET is_teleworking=0 WHERE emp_id=1103')
    result = evaluate_week(client, headers)
    groups = next(day['message_groups'] for day in result['days'] if day['date'] == '2026-10-14')
    assert {group['posture'] for group in groups} == {'DELAYED_OPENING', 'EARLY_DISMISSAL'}
    assert {group['shift_id'] for group in groups} == {1, 2}
    for group in groups:
        recipients = client.get(f"/api/decisions/{group['decision_id']}", headers=headers).json()['recipients']
        assert not any(row['emp_id'] == 1103 and row['included'] for row in recipients)


def test_forecast_wind_only_affects_its_date_and_actual_hours(client, headers, weekly_sources):
    data = weekly_sources[0]
    data['weather']['alerts'] = []
    for period in data['weather']['forecast']:
        if period['startTime'].startswith('2026-10-14T22:'):
            period.update(windSpeed='40 to 45 mph', shortForecast='TEST ONLY thunderstorms')
    result = evaluate_week(client, headers)
    groups = [group for day in result['days'] for group in day['message_groups']]
    assert len(groups) == 1 and groups[0]['date'] == '2026-10-14' and groups[0]['shift_id'] == 2
    assert groups[0]['posture'] == 'TELEWORK'
    decision = client.get(f"/api/decisions/{groups[0]['decision_id']}", headers=headers).json()
    assert '45 mph' in decision['draft_body'] and 'TEST ONLY Wednesday storm' not in decision['draft_body']


def test_incomplete_week_never_invents_missing_day_weather(client, headers, weekly_sources):
    weekly_sources[0]['weather']['forecast'] = weekly_sources[0]['weather']['forecast'][:96]
    result = evaluate_week(client, headers)
    missing = [day for day in result['days'] if day['state'] == 'INSUFFICIENT_DATA']
    assert [day['date'] for day in missing] == ['2026-10-16', '2026-10-17', '2026-10-18']
    assert all(day['posture'] is None and not day['message_groups'] and not day['decision_ids'] for day in missing)


def test_seven_unavailable_days_do_not_call_model_or_save(client, headers, db_path, weekly_sources):
    weekly_sources[0]['weather'] = {'available': False}
    result = client.post('/api/locations/1/evaluate-week', headers=headers).json()
    assert result['state'] == 'INSUFFICIENT_DATA' and len(result['days']) == 7
    assert 'agent' not in weekly_sources[1]
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 0


def test_group_review_stays_in_its_shift_and_essential_staff_need_confirmation(client, headers, weekly_sources):
    result = evaluate_week(client, headers)
    groups = next(day['message_groups'] for day in result['days'] if day['date'] == '2026-10-14')
    early = next(group for group in groups if group['shift_id'] == 1)
    late = next(group for group in groups if group['shift_id'] == 2)
    assert client.post(f"/api/decisions/{early['decision_id']}/recipients", headers=headers, json={'include_ids': [1102]}).status_code == 422
    preview = client.post(f"/api/decisions/{late['decision_id']}/recipients", headers=headers, json={}).json()
    assert {row['emp_id'] for row in preview if row['included']} == {1102, 1105}
    payload = {'status': 'APPROVED', 'posture': 'TELEWORK', 'reason': 'TEST ONLY manager approved this shift-specific action.',
               'draft_subject': 'Wednesday late shift', 'draft_body': 'TEST ONLY manager edited late-shift instructions.', 'essential_confirmed': False}
    url = f"/api/decisions/{late['decision_id']}/review"
    assert client.post(url, headers=headers, json=payload).status_code == 422
    assert client.post(url, headers=headers, json=payload | {'essential_confirmed': True}).status_code == 200
    restored = client.get('/api/locations/1/week-plan', headers=headers).json()
    statuses = {group['decision_id']: group['manager_status'] for day in restored['days'] for group in day['message_groups']}
    assert statuses[late['decision_id']] == 'APPROVED' and statuses[early['decision_id']] == 'PENDING'


def test_week_plan_restores_and_configuration_changes_invalidate_it(client, headers, weekly_sources):
    assert client.get('/api/locations/1/week-plan', headers=headers).json() is None
    result = evaluate_week(client, headers)
    assert client.get('/api/locations/1/week-plan', headers=headers).json() == result
    client.patch('/api/locations/1', headers=headers, json={'latitude': 30.1766})
    assert client.get('/api/locations/1/week-plan', headers=headers).json() is None


def test_week_plan_reflects_manager_edited_posture_and_recipient_count(client, headers, weekly_sources):
    result = evaluate_week(client, headers)
    group = next(day['message_groups'][0] for day in result['days'] if day['message_groups'])
    response = client.post(f"/api/decisions/{group['decision_id']}/review", headers=headers, json={
        'status': 'PENDING', 'posture': 'CLOSED', 'reason': 'TEST ONLY manager changed the shift action.',
        'draft_subject': 'Edited shift notice', 'draft_body': 'TEST ONLY manager edited instructions.',
        'exclude_ids': [1101], 'essential_confirmed': True})
    assert response.status_code == 200
    restored = client.get('/api/locations/1/week-plan', headers=headers).json()
    edited = next(item for day in restored['days'] for item in day['message_groups'] if item['decision_id'] == group['decision_id'])
    assert edited['recipient_count'] == 1 and edited['posture'] == 'CLOSED'


@pytest.mark.parametrize('method,path', [('POST', '/api/locations/1/evaluate-week'), ('GET', '/api/locations/1/week-plan')])
def test_weekly_routes_require_login_and_hide_other_facilities(client, headers, method, path):
    assert client.request(method, path).status_code == 401
    assert client.request(method, path.replace('/1/', '/2/'), headers=headers).status_code == 404


def test_weekly_model_failure_does_not_partially_save_or_change_current_status(client, headers, db_path, weekly_sources, monkeypatch):
    def failure(*args):
        raise ValueError('TEST ONLY model unavailable')
    monkeypatch.setattr(weekly.agent, 'recommend_week', failure)
    result = client.post('/api/locations/1/evaluate-week', headers=headers).json()
    assert result['state'] == 'INSUFFICIENT_DATA'
    with closing(connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 0
        assert db.execute('SELECT last_checked_at FROM locations WHERE id=1').fetchone()[0] is None


@pytest.mark.parametrize('change', ['missing_date', 'duplicate_date', 'wrong_posture'])
def test_weekly_adviser_rejects_fabricated_or_duplicate_dates_and_postures(monkeypatch, change):
    contexts = {'2026-10-12': policy_context(LOCATION, signals(), NOW)}
    item = {'date': '2026-10-12', 'posture': 'NORMAL', 'reason': 'TEST ONLY bounded weekly explanation.',
            'confidence': .5, 'uncertainty': 'Synthetic', 'what_would_change': ['New alert']}
    if change == 'missing_date':
        item['date'] = '2026-10-13'
    if change == 'wrong_posture':
        item['posture'] = 'CLOSED'
    answer = {'days': [item, item] if change == 'duplicate_date' else [item]}
    calls = []
    def build(output_model):
        assert output_model is WeeklyAdvice
        def invoke(prompt, **kwargs):
            calls.append(json.loads(prompt))
            return json.dumps(answer)
        return invoke
    monkeypatch.setattr(agent, 'build_agent', build)
    with pytest.raises(ValueError):
        agent.recommend_week(LOCATION, contexts)
    assert len(calls) == 1 and 'employees' not in calls[0]


@pytest.mark.parametrize('stream', [True, False])
def test_weekly_real_strands_adapter_validates_seven_dates_in_one_call(monkeypatch, stream):
    import httpx2
    _, contexts = weekly.week_contexts(LOCATION, week_signals(), NOW)
    answer = {'days': [{'date': day, 'posture': context['posture'],
        'reason': f'TEST ONLY dated forecast explanation for {day}.', 'confidence': .8,
        'uncertainty': 'Synthetic source forecast', 'what_would_change': ['New source data']}
        for day, context in contexts.items()]}
    calls = []
    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body['response_format']['json_schema']['schema'] == WeeklyAdvice.model_json_schema()
        assert {item['function']['name'] for item in body['tools']} == {'get_nws_weather', 'get_air_quality'}
        assert 'emp_id' not in json.dumps(body['messages'])
        content = json.dumps(answer)
        if stream:
            chunks = [{'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]},
                      {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
            events = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            return httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, text=events)
        return httpx2.Response(200, json={'id': 'test-week', 'object': 'chat.completion', 'created': 0,
            'model': 'test-model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    original = agent.OpenAIModel
    def model(**kwargs):
        kwargs['client_args']['http_client'] = httpx2.AsyncClient(transport=httpx2.MockTransport(provider))
        return original(**kwargs, stream=stream)
    monkeypatch.setattr(agent, 'OpenAIModel', model)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('MODEL_ID', 'test-model')
    results = agent.recommend_week(LOCATION, contexts)
    assert len(calls) == 1 and set(results) == set(contexts) and len(results) == 7
    for day, result in results.items():
        assert result.posture == contexts[day]['posture']
        assert result.confidence <= contexts[day]['confidence_cap']
        assert result.affected_start.isoformat() == contexts[day]['affected_start']


def test_weekly_selection_uses_one_roster_query_and_saved_counts_match(db_path, weekly_sources):
    statements = []
    with closing(connect(db_path)) as db:
        office = dict(db.execute('SELECT * FROM locations WHERE id=1').fetchone())
        db.set_trace_callback(statements.append)
        result = weekly.run_weekly_evaluation(db, office)
        roster_reads = [query for query in statements if 'LEFT JOIN shifts s ON s.id = u.shift_id' in query]
        assert len(roster_reads) == 1
        for day in result['days']:
            for group in day['message_groups']:
                row = db.execute('SELECT recipients_json FROM decisions WHERE id=?', (group['decision_id'],)).fetchone()
                saved = json.loads(row[0])
                assert group['recipient_count'] == sum(item['included'] for item in saved)
                assert all(item['shift_id'] == group['shift_id'] for item in saved)


def test_mixed_overlapping_hazards_use_strongest_action_and_preserve_safe_gap():
    segments = weekly.impact_segments({'impact_windows': [
        {'start': '2026-10-14T13:00:00Z', 'end': '2026-10-14T15:00:00Z', 'posture': 'TELEWORK'},
        {'start': '2026-10-14T09:00:00-05:00', 'end': '2026-10-14T11:00:00-05:00', 'posture': 'CLOSED'},
        {'start': '2026-10-14T22:00:00Z', 'end': '2026-10-14T23:00:00Z', 'posture': 'EARLY_DISMISSAL'}]})
    assert [(item['start'].hour, item['end'].hour, item['posture']) for item in segments] == [
        (13, 14, 'TELEWORK'), (14, 16, 'CLOSED'), (22, 23, 'EARLY_DISMISSAL')]


def test_weekly_prompt_deduplicates_alert_text_preserves_per_date_assessments_and_full_sources(monkeypatch):
    import copy
    data = week_signals()
    data['weather']['alerts'][0]['instruction'] = 'TEST ONLY shared alert instructions'
    _, contexts = weekly.week_contexts(LOCATION, data, NOW)
    original = copy.deepcopy(contexts)
    calls = []
    answer = {'days': [{'date': day, 'posture': context['posture'], 'reason': 'TEST ONLY dated explanation of actual hazards.',
        'confidence': .5, 'uncertainty': 'Synthetic fixture', 'what_would_change': ['New warning']}
        for day, context in contexts.items()]}
    def model(output_model):
        def invoke(prompt, **kwargs):
            calls.append(prompt)
            return json.dumps(answer)
        return invoke
    monkeypatch.setattr(agent, 'build_agent', model)
    results = agent.recommend_week(LOCATION, contexts)
    assert len(calls) == 1
    compact = json.loads(calls[0])
    assert calls[0].count('TEST ONLY shared alert instructions') == 1
    assert len(compact['weather_alerts']) == 1 and len(compact['days']) == 7
    assessments = {item['date']: item['policy']['weather_alerts'][0]['demo_policy_assessment'] for item in compact['days']}
    assert 'Outside' in assessments['2026-10-12']
    assert 'Meets demo office trigger' in assessments['2026-10-14']
    assert len(calls[0]) < len(json.dumps(contexts)) * .6
    assert contexts == original
    assert set(results) == set(contexts)
    assert all(results[day].evidence == [Evidence(**entry) for entry in contexts[day]['evidence']] for day in results)
