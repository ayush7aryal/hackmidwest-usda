# SendGrid HTTPS integration

The backend supports SendGrid's official Python SDK (`sendgrid`, installed and
locked with uv). It submits to `https://api.sendgrid.com/v3/mail/send` using
outbound HTTPS on port 443. It does not use SMTP ports or a Gmail App Password.
No additional database tables, services, agents or hazard tools are added.

## What you need to provide

1. A SendGrid API key with **Mail Send** permission. Put it in `.env` privately
   as `SENDGRID_API_KEY`; do not paste the key in chat or commit it.
2. A verified sender address for `EMAIL_FROM`. For this hackathon test you can
   verify an address you own using **Settings → Sender Authentication → Single
   Sender Verification**. For production, authenticate a domain you control.
3. The demo destination. Existing numbered Gmail aliases can remain as employee
   addresses. Optionally set `DEMO_EMAIL_TO=aryalayush7@gmail.com` to redirect
   each decision to one test inbox instead of its individual aliases.

See the official [API key setup](https://www.twilio.com/docs/sendgrid/ui/account-and-settings/api-keys)
and [sender verification requirements](https://www.twilio.com/docs/sendgrid/for-developers/sending-email/sender-identity).

## Configuration

```dotenv
EMAIL_ENABLED=false
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=
EMAIL_FROM=YOUR_VERIFIED_SENDER
DEMO_EMAIL_TO=
```

Keep delivery disabled until the key and sender are configured. To activate
later, set `EMAIL_ENABLED=true`, rebuild the API image for the new SDK, and
recreate the services:

```sh
docker compose build api
docker compose up -d --wait
docker compose exec -T web nginx -s reload
```

On Akamai, run from `/root/facility-operations` and add
`-f docker-compose.yml -f docker-compose.akamai.yml` to each Compose command.
The persistent database volume is preserved; do not rerun the baseline seed.

## How sending works

The judging frontend still provides preparation, approval and download only.
It does not expose email controls. The authenticated backend already has:

- `GET /api/email-status`: configuration readiness, without an API call or
  disclosure of the key. This does not verify account access or sender identity.
- `POST /api/decisions/{id}/send`: explicit submission of a saved, approved
  message. It rechecks current ownership, staff schedules, essential instructions,
  expiry, facility configuration and duplicate notifications.

SendGrid receives one request with separate recipient envelopes so employees
cannot see one another's addresses. The existing `decisions.delivery_json`
records the provider, HTTP status, provider message ID and accepted employee IDs.
HTTP **202** means accepted for processing, not confirmed inbox delivery.
Definite client rejections allow an explicit retry after correction; timeouts,
HTTP 408, server errors and unexpected responses remain UNKNOWN and block blind
retries. There are no automatic retries or fallback sends through SMTP.

The scheduler and Strands agent never call the send endpoint. SMTP remains an
optional alternative with `EMAIL_PROVIDER=smtp` and the existing SMTP settings.
Switching providers does not bypass a previous accepted or uncertain submission.

Tests exercise the real SDK request construction with a mocked HTTP transport.
Real delivery needs the account key, a verified sender and a live provider test.
