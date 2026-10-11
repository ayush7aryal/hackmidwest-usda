"""SendGrid HTTPS transport; no SMTP sockets, background sends or retries."""

from email.message import EmailMessage

from python_http_client.exceptions import HTTPError
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Personalization, To


class SendGridResponseError(Exception):
    """Expose a status code without retaining provider bodies or credentials."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"SendGrid HTTP {status_code}")


def submit_sendgrid(config: dict, message: EmailMessage, targets: list[str]) -> dict:
    """One private envelope per recipient in one bounded HTTPS request."""
    mail = Mail(from_email=config['sender'], subject=str(message['Subject']),
                plain_text_content=message.get_content())
    for target in targets:
        envelope = Personalization()
        envelope.add_to(To(target))
        mail.add_personalization(envelope)
    client = SendGridAPIClient(api_key=config['api_key'])
    try:
        response = client.client.mail.send.post(request_body=mail.get(), timeout=20)
    except HTTPError as error:
        raise SendGridResponseError(error.status_code) from None
    if response.status_code != 202:
        raise SendGridResponseError(response.status_code)
    provider_id = next((value for key, value in response.headers.items()
                        if key.lower() == 'x-message-id'), None)
    return {'http_status': 202, 'provider_message_id': provider_id, 'refused': {}}
