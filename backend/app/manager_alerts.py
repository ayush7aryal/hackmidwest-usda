"""Automatic manager review requests; employee delivery remains a separate action."""

import json
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.db import utc_now
from app.email_delivery import delivery_message, delivery_outcome, delivery_settings
from app.thresholds import timestamp


def review_template(row: dict, location: dict, managers: list[dict], scheduled: bool = False) -> dict:
    """Use saved advice and the facility's local timezone in the review request."""
    base = os.environ.get('APP_BASE_URL', 'http://localhost:8080').strip().rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.fragment or parsed.query:
        raise ValueError('Configure APP_BASE_URL with the dashboard URL')
    zone = ZoneInfo(location['timezone'])
    start = timestamp(row['affected_start']).astimezone(zone).strftime('%a %b %d, %Y %I:%M %p %Z')
    end = timestamp(row['affected_end']).astimezone(zone).strftime('%a %b %d, %Y %I:%M %p %Z')
    names = ', '.join(manager['name'] for manager in managers)
    accounts = '\n'.join(f"- {m['name']} | Manager ID (login): {m['emp_id']} | {m['email']}" for m in managers)
    url = f"{base}/#decision/{row['id']}"
    posture = row['posture'].replace('_', ' ')
    previous = location['status'].replace('_', ' ')
    subject = (f"Scheduled update: {location['name']} — {posture}" if scheduled else
               f"Please review: {location['name']} — {previous} → {posture}")
    summary = (f"The scheduled check is complete. Previous status: {previous}. Recommended status: {posture}." if scheduled else
               f"{location['name']} (facility {location['id']}) has changed from {previous} to {posture}.")
    return {'subject': subject,
            'to': [m['email'] for m in managers], 'managers': managers, 'dashboard_url': url,
            'body': f"Hello {names},\n\n"
                    f"{summary}\n"
                    f"This is an AI-assisted recommendation awaiting your decision.\n\n"
                    f"Affected window: {start} – {end}\n"
                    f"Evaluated at: {row['evaluated_at']}\n\n"
                    f"Reason: {row['reason']}\n\n"
                    f"Please review the risk and source evidence in the dashboard:\n{url}\n\n"
                    "Choose the final operational action, check the affected employees, and review the message. "
                    "Click Approve and send email only if you decide to notify them. "
                    "You may also reject the recommendation or leave it pending.\n"
                    "No employee emails have been sent by this status check.\n\n"
                    f"Assigned manager accounts:\n{accounts}\n"}


def store_alert(db, decision_id: int, alternatives: dict, alert: dict) -> None:
    """Use the existing decision JSON and manager timestamp, never employee fields."""
    alternatives['manager_alert'] = alert
    db.execute('UPDATE decisions SET alternatives_json = ?, manager_alert_sent_at = ? WHERE id = ?',
               (json.dumps(alternatives), alert.get('accepted_at'), decision_id))


def prepare_alert(db, row: dict, location: dict, scheduled: bool = False) -> tuple[dict, tuple | None]:
    """Check the exact transition and construct a manager-only delivery."""
    alert = {'status': 'SUPPRESSED', 'reason': 'No NORMAL-to-disruption status change.'}
    summary = scheduled and os.environ.get('MANAGER_SCHEDULED_UPDATES_ENABLED', 'false').lower() == 'true'
    if not summary and (location['status'] != 'NORMAL' or row['posture'] == 'NORMAL'):
        return alert, None
    alert = {'status': 'FAILED'}
    managers = [dict(m) for m in db.execute(
        'SELECT emp_id, name, email FROM users WHERE location_id = ? AND is_manager = 1 ORDER BY emp_id',
        (row['location_id'],))]
    try:
        if not managers:
            raise ValueError('No manager is assigned to this facility')
        alert = {'status': 'DISABLED', 'scheduled': scheduled,
                 'template': review_template(row, location, managers, scheduled=summary)}
        if os.environ.get('MANAGER_ALERTS_ENABLED', 'false').lower() != 'true':
            return alert, None
        config = delivery_settings() | {'redirect': None}  # Always notify the assigned managers.
        message, targets = delivery_message(config, row | {
            'draft_subject': alert['template']['subject'], 'draft_body': alert['template']['body']}, managers)
    except ValueError as error:
        return alert | {'status': 'FAILED', 'error': str(error)}, None
    except Exception as error:
        return alert | {'status': 'FAILED', 'error': type(error).__name__}, None
    alert.update(status='IN_PROGRESS', provider=config['provider'], requested_at=utc_now(), targets=targets)
    return alert, (config, message, targets)


def notify_manager(db, decision_id: int, scheduled: bool = False) -> dict:
    """Claim once after a committed NORMAL-to-disruption evaluation, then submit."""
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = dict(db.execute('SELECT * FROM decisions WHERE id = ?', (decision_id,)).fetchone())
        alternatives = json.loads(row['alternatives_json'])
        previous = alternatives.get('manager_alert')
        if isinstance(previous, dict):
            return previous
        alternatives['evaluation_origin'] = 'scheduled' if scheduled else 'manual'
        alert, submission = prepare_alert(db, row, alternatives['facility_snapshot'], scheduled=scheduled)
        store_alert(db, decision_id, alternatives, alert)
    if submission is None:
        return alert
    # No database write lock is held during the external request.
    alert.update(delivery_outcome(*submission))
    if alert['accepted_addresses']:
        alert['accepted_at'] = alert['completed_at']
    with db:
        store_alert(db, decision_id, alternatives, alert)
    return alert
