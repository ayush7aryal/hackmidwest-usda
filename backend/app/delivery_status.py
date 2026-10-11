"""Read SendGrid activity without sending mail or changing approval safeguards."""

import json
import os
import re
from datetime import UTC, datetime

from fastapi import HTTPException
from python_http_client.exceptions import HTTPError
from sendgrid import SendGridAPIClient

from app.db import utc_now
from app.review import owned_decision
from app.thresholds import timestamp


def provider_activity(delivery: dict) -> dict:
    """Match this exact submission and recipient; never infer delivery from 202."""
    prefix = delivery.get('provider_message_id', '')
    if not prefix or not re.fullmatch(r'[A-Za-z0-9_-]+', prefix):
        return {'available': False, 'message': 'No provider tracking ID is available.'}
    try:
        client = SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY', ''))
        response = client.client.messages.get(query_params={
            'query': f'msg_id LIKE "{prefix}%"', 'limit': 1000}, timeout=10)
        messages = json.loads(response.body)['messages']
        targets = set(delivery.get('targets', []))
        receipts = [{key: message.get(key) for key in ('to_email', 'status', 'last_event_time')}
                    for message in messages if message.get('msg_id', '').startswith(prefix + '.')
                    and message.get('to_email') in targets]
        return {'available': True, 'receipts': receipts,
                'message': 'Mail-server delivery does not confirm inbox placement. Check Spam and All Mail.'}
    except HTTPError as error:
        return {'available': False, 'message': f'SendGrid activity unavailable (HTTP {error.status_code}).'}
    except Exception:
        return {'available': False, 'message': 'Delivery activity is temporarily unavailable; no email was resent.'}


def refresh_delivery(db, decision_id: int, manager_id: int) -> dict:
    """Persist delivery evidence in the same table, without overwriting a resend."""
    row, _ = owned_decision(db, decision_id, manager_id)
    delivery = json.loads(row['delivery_json'] or '{}')
    if delivery.get('provider') != 'sendgrid' or delivery.get('status') not in ('ACCEPTED', 'PARTIAL'):
        raise HTTPException(409, 'No accepted SendGrid submission to check.')
    previous = delivery.get('activity', {})
    if previous.get('checked_at') and (datetime.now(UTC) - timestamp(previous['checked_at'])).total_seconds() < 15:
        return delivery
    activity = provider_activity(delivery) | {'checked_at': utc_now()}
    # Retain previously confirmed receipts during provider outages.
    if not activity['available']:
        activity['receipts'] = previous.get('receipts', [])
    delivery['activity'] = activity
    with db:
        updated = db.execute('UPDATE decisions SET delivery_json=? WHERE id=? AND delivery_json=?',
                             (json.dumps(delivery), decision_id, row['delivery_json']))
    if updated.rowcount != 1:
        raise HTTPException(409, 'The message changed during the check; reload its latest delivery record.')
    return delivery
