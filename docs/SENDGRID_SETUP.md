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
MANAGER_ALERTS_ENABLED=false
APP_BASE_URL=http://localhost:8080
EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=
EMAIL_FROM=YOUR_VERIFIED_SENDER
DEMO_EMAIL_TO=
```

Keep delivery disabled until the key and sender are configured. To activate
later, set `EMAIL_ENABLED=true` and `MANAGER_ALERTS_ENABLED=true`, and set
`APP_BASE_URL` to the public dashboard URL on Akamai. Rebuild the images and
recreate the services:

```sh
docker compose build api web
docker compose up -d --wait
docker compose exec -T web nginx -s reload
```

On Akamai, run from `/root/facility-operations` and add
`-f docker-compose.yml -f docker-compose.akamai.yml` to each Compose command.
The persistent database volume is preserved; do not rerun the baseline seed.

## How sending works

Daily manual and three-hour scheduled checks compare the saved facility status
with the validated recommendation. Only **NORMAL → DELAYED_OPENING /
EARLY_DISMISSAL / TELEWORK / CLOSED** automatically sends a manager review email.
Repeated checks with the same status and returns to NORMAL do not. Weekly
planning saves future drafts without changing current status or sending email.

Manager email goes to the current managers assigned in `users`, independent of
the optional employee demo redirect. It includes their names, login IDs and email
addresses, facility, affected local times, rationale and a direct decision link.
The dashboard shows the exact template under **Risk → Manager review email**
and the signed-in account email in the header. Claim/result metadata lives in
`decisions.alternatives_json.manager_alert`, and acceptance time in
`manager_alert_sent_at`; employee delivery fields stay untouched. Claims are
committed before the HTTPS request, and provider errors cannot undo the saved
recommendation. A failed or uncertain manager submission is visible in the
dashboard; there are no blind automatic retries.

The manager may leave the decision pending, reject it, or review staff and approve
a message. **Approve and send email** first saves the approval and then submits
the message, with employee addresses rechecked at send time. Save draft and
Reject never send. Previously approved unsent messages have a send button too.
The backend review endpoint itself only records approval; the combined dashboard
action explicitly calls the send endpoint afterward, and stops if review fails.
The authenticated backend has:

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
