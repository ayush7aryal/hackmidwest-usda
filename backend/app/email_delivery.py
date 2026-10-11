"""Explicit manager-triggered email delivery using the existing decision record."""

import json
import os
import smtplib
import ssl
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import UTC, datetime

from fastapi import HTTPException

from app.db import utc_now
from app.models import RecipientSelection, ReviewRequest
from app.notification_rules import already_notified
from app.review import owned_decision, recipient_preview, validate_approval
from app.sendgrid_delivery import SendGridResponseError, submit_sendgrid


def address(value: str) -> str:
    """Accept one mailbox, rejecting header injection and malformed addresses."""
    if not value or '\n' in value or '\r' in value:
        raise ValueError("A complete email address is required")
    try:
        parsed = Address(addr_spec=value)
    except (ValueError, HeaderParseError):
        raise ValueError("A complete email address is required") from None
    if not parsed.username or not parsed.domain or '.' not in parsed.domain:
        raise ValueError("A complete email address is required")
    return parsed.addr_spec


class SMTPSetupError(Exception):
    """Connection/authentication failed before any message could be submitted."""


def smtp_settings() -> dict:
    """Secrets stay on the server; require encrypted SMTP for provider delivery."""
    if os.environ.get("EMAIL_ENABLED", "false").lower() != "true":
        raise ValueError("Email sending is disabled")
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise ValueError("Configure the SMTP provider before sending")
    if os.environ.get('SMTP_USERNAME') and not os.environ.get('SMTP_PASSWORD'):
        raise ValueError('Configure the SMTP credential before sending')
    port = int(os.environ.get("SMTP_PORT", "587"))
    if port not in (465, 587, 2525):
        raise ValueError("Use encrypted SMTP on port 465, 587 or 2525")
    sender = address(os.environ.get("EMAIL_FROM", ""))
    redirect = os.environ.get("DEMO_EMAIL_TO", "").strip()
    password = os.environ.get('SMTP_PASSWORD', '')
    if host == 'smtp.gmail.com':
        password = ''.join(password.split())
    return {"host": host, "port": port, "sender": sender,
            "username": os.environ.get("SMTP_USERNAME", ""),
            "password": password,
            "redirect": address(redirect) if redirect else None}


def delivery_settings() -> dict:
    """Select one configured provider; never silently fall back after a failure."""
    provider = os.environ.get('EMAIL_PROVIDER', 'smtp').strip().lower()
    if provider == 'smtp':
        return smtp_settings() | {'provider': 'smtp'}
    if provider != 'sendgrid':
        raise ValueError('EMAIL_PROVIDER must be smtp or sendgrid')
    if os.environ.get('EMAIL_ENABLED', 'false').lower() != 'true':
        raise ValueError('Email sending is disabled')
    key = os.environ.get('SENDGRID_API_KEY', '').strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError('Configure SENDGRID_API_KEY before sending')
    redirect = os.environ.get('DEMO_EMAIL_TO', '').strip()
    return {'provider': 'sendgrid', 'api_key': key,
            'sender': address(os.environ.get('EMAIL_FROM', '')),
            'redirect': address(redirect) if redirect else None}


def email_status() -> dict:
    """Expose readiness without leaking provider credentials."""
    try:
        config = delivery_settings()
        return {"ready": True, "demo_redirect": bool(config["redirect"]),
                "provider": config['provider'],
                "message": "Demo email goes to the configured test inbox." if config["redirect"] else "Email provider configured; connection is checked on send."}
    except (ValueError, TypeError):
        return {"ready": False, "demo_redirect": False, "message": "Email setup required: configure an enabled provider and sender."}


def delivery_message(config: dict, decision: dict, recipients: list[dict]) -> tuple[EmailMessage, list[str]]:
    """Hide employee addresses from one another; redirect synthetic demo mail explicitly."""
    intended = sorted({address(row["email"]) for row in recipients})
    if not config["redirect"] and any(mail.rsplit('@', 1)[1].lower() in ('example.com', 'example.org', 'example.net') for mail in intended):
        raise HTTPException(409, "Synthetic employees need DEMO_EMAIL_TO; configure your real demo inbox")
    targets = [config["redirect"]] if config["redirect"] else intended
    message = EmailMessage()
    message['From'] = config["sender"]
    message['To'] = config["redirect"] or 'undisclosed-recipients:;'
    message['Subject'] = ('[DEMO] ' if config["redirect"] else '') + decision['draft_subject']
    message['Date'] = format_datetime(datetime.now(UTC))
    message['Message-ID'] = make_msgid(idstring=f"facility-decision-{decision['id']}")
    body = decision['draft_body']
    if config["redirect"]:
        body = "DEMO DELIVERY — synthetic employee addresses were not contacted.\nPlanned recipients: " + ', '.join(intended) + '\n\n' + body
    message.set_content(body)
    return message, targets


def submit_smtp(config: dict, message: EmailMessage, targets: list[str]) -> dict:
    """No transport retries: uncertain SMTP outcomes must not cause duplicate messages."""
    context = ssl.create_default_context()
    client = None
    try:
        try:
            client = (smtplib.SMTP_SSL(config['host'], config['port'], timeout=20, context=context)
                      if config['port'] == 465 else smtplib.SMTP(config['host'], config['port'], timeout=20))
            if config['port'] != 465:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            if config['username']:
                client.login(config['username'], config['password'])
        except (OSError, smtplib.SMTPException) as error:
            raise SMTPSetupError(type(error).__name__) from None
        return client.send_message(message, from_addr=config['sender'], to_addrs=targets)
    finally:
        # A disconnect during QUIT must not turn an accepted message into a retry.
        if client:
            client.close()


def approved_recipients(db, row: dict, location: dict) -> list[dict]:
    """Reload staff and refuse stale, unapproved or duplicate submissions."""
    delivery = json.loads(row['delivery_json'] or '{}')
    if row['manager_status'] != 'APPROVED':
        raise HTTPException(409, "Approve the saved message before sending")
    if row['sent_at'] or delivery.get('status') in ('IN_PROGRESS', 'UNKNOWN', 'PARTIAL', 'ACCEPTED'):
        raise HTTPException(409, "This message was already submitted; check its delivery status")
    alternatives = json.loads(row['alternatives_json'])
    selection = RecipientSelection(**alternatives.get('recipient_selection', {}))
    fresh = recipient_preview(db, row, location, selection, row['posture'])
    recipients = [employee for employee in fresh if employee['included']]
    saved = [employee for employee in json.loads(row['recipients_json']) if employee['included']]
    signature = lambda rows: {(r['emp_id'], r['email'], r['requires_manager_review']) for r in rows}
    if signature(recipients) != signature(saved):
        raise HTTPException(409, "Staff selection changed; refresh recipients and approve again")
    review = alternatives.get('reviews', [{}])[-1]
    body = ReviewRequest(status='APPROVED', posture=row['posture'], reason=row['reason'],
                         draft_subject=row['draft_subject'], draft_body=row['draft_body'],
                         essential_confirmed=review.get('essential_confirmed', False),
                         **selection.model_dump(exclude={'posture'}))
    validate_approval(row, location, alternatives, fresh, body)
    if not recipients:
        raise HTTPException(409, "No notification needed: normal/unchanged operations or no affected staff")
    if already_notified(db, row, recipients):
        raise HTTPException(409, "Some recipients already received this action for this window; refresh and exclude them before approving again")
    return recipients


def submit_delivery(config: dict, message: EmailMessage, targets: list[str]) -> dict:
    """Keep transport selection separate from approval and audience rules."""
    if config['provider'] == 'sendgrid':
        return submit_sendgrid(config, message, targets)
    return {'refused': submit_smtp(config, message, targets)}


def delivery_outcome(config: dict, message: EmailMessage, targets: list[str]) -> dict:
    """Record provider acceptance separately from confirmed inbox delivery."""
    outcome = {'accepted_addresses': []}
    try:
        result = submit_delivery(config, message, targets)
        refused = result.pop('refused')
        accepted = [target for target in targets if target not in refused]
        outcome.update(result, accepted_addresses=accepted,
                       status='FAILED' if not accepted else 'PARTIAL' if refused else 'ACCEPTED',
                       refused_addresses=list(refused))
    except SendGridResponseError as error:
        rejected = 400 <= error.status_code < 500 and error.status_code != 408
        outcome.update(status='FAILED' if rejected else 'UNKNOWN',
                       http_status=error.status_code, error=str(error))
    except (SMTPSetupError, smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as error:
        outcome.update(status='FAILED', error=type(error).__name__)
    except Exception as error:
        # After submitting, an unexpected failure must also block duplicate retries.
        outcome.update(status='UNKNOWN', error=type(error).__name__)
    return outcome | {'completed_at': utc_now()}


def send_decision(db, decision_id: int, manager_id: int) -> dict:
    """Persist the claim before submission; keep uncertain outcomes inspectable."""
    try:
        config = delivery_settings()
    except (ValueError, TypeError) as error:
        raise HTTPException(503, str(error)) from None
    with db:
        db.execute('BEGIN IMMEDIATE')
        row, location = owned_decision(db, decision_id, manager_id)
        recipients = approved_recipients(db, row, location)
        try:
            message, targets = delivery_message(config, row, recipients)
        except ValueError:
            raise HTTPException(422, "Invalid email address or message header; correct it before sending") from None
        delivery = {'status': 'IN_PROGRESS', 'provider': config['provider'],
                    'requested_at': utc_now(), 'requested_by': manager_id,
                    'demo_redirect': bool(config['redirect']), 'targets': targets,
                    'intended_employee_ids': [employee['emp_id'] for employee in recipients],
                    'message_id': str(message['Message-ID'])}
        db.execute('UPDATE decisions SET delivery_json = ? WHERE id = ?', (json.dumps(delivery), decision_id))
    delivery.update(delivery_outcome(config, message, targets))
    accepted = delivery['accepted_addresses']
    if accepted:
        delivery['accepted_employee_ids'] = [employee['emp_id'] for employee in recipients
                                             if config['redirect'] or employee['email'] in accepted]
    with db:
        db.execute('UPDATE decisions SET delivery_json = ?, sent_at = ? WHERE id = ?',
                   (json.dumps(delivery), delivery['completed_at'] if accepted else None, decision_id))
    return delivery
