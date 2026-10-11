# Facility Operations AI

A hackathon MVP for manager-reviewed US office disruption advice: FastAPI,
SQLite, one Strands agent, two live data tools, React/Vite, and two Docker
containers. Built from [the blueprint](docs/HACKATHON_IMPLEMENTATION.md).
**Manager first, employees only by choice.** When a daily check changes a facility
from NORMAL to a disruption, SendGrid automatically emails its assigned managers
a review request and dashboard link. The manager reviews the risk, selects staff,
and clicks **Approve and send email** to save approval and submit it to those
employees. Saving a draft, scheduled checks and the agent never send employee
messages. Previously approved unsent messages also have an explicit send button.

## Run the complete application locally

Requires Docker Desktop/Engine with Compose:

```sh
cp .env.example .env
# Edit .env: configure OPENAI_API_KEY, MODEL_ID and NWS_USER_AGENT.
docker compose build
docker compose run --rm api python seed.py
docker compose up -d
```

Open **http://localhost:8080**. Sign in as manager **1001** (Chicago) or **2001**
(Denver). Leave the password blank only in local demo mode with an empty
`MANAGER_PASSWORD`; a configured password is always required. Nginx proxies the API;
there is no published backend port.

Set `MODEL_ID` to a chat/tool-capable model available to your account and provide
`OPENAI_API_KEY`. `OPENAI_BASE_URL` optionally selects a provisioned compatible
endpoint, including Akamai inference if supported. Missing credentials or invalid
source/model output produce `INSUFFICIENT_DATA`, preserving the last known
recommendation. There is no fake-live or silent deterministic fallback.

The example configuration uses the [Akamai workshop inference service](https://github.com/akamai-developers/akamai-workshop-hackmidwest/blob/e362ae25ea54a5ee33fa11f6265d15bc1705dca3/docs/inference.md):
`MODEL_ID=hackathon-chat`, `OPENAI_BASE_URL=https://ws.akamai-workshops.com/v1`, and
your team key in `OPENAI_API_KEY`. The agent requests JSON schema output with a
4,096-token limit. For direct OpenAI, clear the base URL and choose its model.

After editing `.env`, run `docker compose up -d --force-recreate api` so the
backend loads the new settings. A container restart alone retains its old environment.

## Akamai deployment

Follow [the Akamai deployment runbook](docs/AKAMAI_DEPLOYMENT.md): Ubuntu, Docker,
source transfer, model/password configuration, DNS/TLS, Compose, backups and
verification. Application processing and SQLite stay on the Compute Instance.
The override forces password login and adds HTTPS to Nginx with host-provided
certificates, retaining exactly two services. A source-only deployment archive is
available at `artifacts/facility-operations-akamai.tar.gz`; it excludes keys and
local data. The current deployment is **https://172.236.119.150** on a Chicago
Ubuntu 24.04 instance. It uses an automatically renewed IP-address certificate;
no domain is required. Deployment details and operator commands are in the runbook.

## Demo workflow

1. Sign in and choose **Check next 7 days**. **Edit location** opens a focused
   form with a separate facility name, searchable map, ID, coordinates, timezone and hours.
   Find a US city/ZIP code or paste `latitude, longitude`; choose a result, click
   the map or drag the pin to refine the site. Manual fields remain editable.
   Place results set the timezone; check it when using coordinates or the map.
2. The week strip highlights affected shifts and missing coverage. Select one
   date to see its recommendation and shift drafts; pending drafts open first.
3. Open a draft and follow **Risk → Recipients → Message**. Choose the operational
   action, review the selected staff, then edit the subject and message.
4. **Excluded** staff remain available for inspection. Facility-wide scope
   explicitly widens the audience. Confirm essential-staff instructions before
   approving. A failed recipient refresh blocks approval until retried.
5. Save, approve or reject. Download the saved message with its recipient list.
   Approved message bodies and downloads omit draft/approval notices; review
   status stays in the manager UI and audit record. Past checks and decision
   records are expandable.

Urgent NWS instructions stay visible; advisory details, source notes, policy and
confidence are available on demand. Missing coverage and outdated alerts remain
explicitly labeled. Refresh failures retain the previous weekly check with a
warning. Unsaved draft edits are protected when leaving the review flow. The
operational action is emphasized in Risk; Message focuses on wording and recipients.
An animated loader and elapsed time appear during today/week evaluations, with
duplicate requests disabled. Both live hazard sources are fetched concurrently;
the agent receives compact forecasts and shared alert text once per weekly check.
Full source snapshots and exact selection windows remain unchanged. Model calls
do not silently retry; failures preserve saved recommendations and allow a retry.

The map uses Leaflet 1.9.4 and attributed [OpenStreetMap tiles](https://operations.osmfoundation.org/policies/tiles/).
Explicit place searches use [Open-Meteo geocoding](https://open-meteo.com/en/docs/geocoding-api)
(GeoNames data); coordinate searches stay in the browser. Map/place-search
requests require internet access. A search/tile failure leaves manual entry usable.
This facility-setup lookup is separate from the two Strands hazard tools; it is
never used as weather evidence. Leaflet is the only new runtime dependency,
needed for interactive pinning; no database table or Docker service was added.

Creating a facility reuses the manager's current synthetic employee sheet: the
manager and existing staff move to the new facility with the same employee IDs,
names, emails, shifts and essential/remote flags. No duplicate staff are created;
the other manager's roster remains at their facility. Past decisions stay with
their original facility and retain their frozen employee snapshots. A fresh
evaluation filters this same roster using the new facility's timezone, operating
hours, status and affected window. ID edits atomically move related users/decisions. Configuration
changes require a fresh evaluation before approving old advice. Office/shift
hours are same-day; impact windows can cross midnight. Matching uses actual local
dates and IANA timezone/DST offsets.

## Five-location demo and Gmail

For delivery through HTTPS instead of SMTP, follow
[SendGrid setup](docs/SENDGRID_SETUP.md). The official SDK is installed through
uv; configuration requires `EMAIL_PROVIDER=sendgrid`, `SENDGRID_API_KEY` and a
verified `EMAIL_FROM`. No SMTP-port access or Gmail App Password is required.

The demo exposes message preparation, approval, download and explicit employee
sending. Enable `EMAIL_ENABLED=true` and `MANAGER_ALERTS_ENABLED=true`, and set
`APP_BASE_URL` to the dashboard origin (locally `http://localhost:8080`). The SMTP
settings below remain an optional alternative; SendGrid uses HTTPS instead.

Run the additive demo command after building the current backend image:

```sh
docker compose run --rm api python seed.py --expand-demo --demo-inbox YOUR_GMAIL@gmail.com
```

On Akamai, use both Compose files and the existing project directory. The command
preserves facilities, IDs and decision history. It adds these separate accounts:

| Manager ID | Manager | Facility | Latitude, longitude |
| --- | --- | --- | --- |
| 51001 | Maya Patel | Panama City Beach | 30.1766, -85.8055 |
| 52001 | Daniel Brooks | Jacksonville | 30.3322, -81.6557 |
| 53001 | Sofia Ramirez | Charleston | 32.7765, -79.9311 |
| 54001 | Ethan Chen | Savannah | 32.0809, -81.0912 |
| 55001 | Olivia Bennett | Tampa | 27.9506, -82.4572 |

All accounts use the configured manager password. Each new facility has 16
fictional employees with realistic names. Daily shifts cover 00:00–06:00,
06:00–12:00, 12:00–18:00 and 18:00–23:59, with on-site, essential and remote
examples in every daily shift. Additional weekday and weekend shifts make the
exclusions visible. Daily crews work all seven days, including holiday dates;
there is no separate holiday calendar. Same-day hours leave the final minute
before midnight outside the MVP operating window.

These cities had live NWS advisories/watches/warnings when selected. Future
weather can change; the demo never injects fake live hazards. Managers can choose
another action in Risk to demonstrate selection during normal weather.

`--demo-inbox` assigns **all synthetic employees** numbered Gmail aliases such
as `YOUR_GMAIL+1@gmail.com`. The first existing manager uses the base address;
other managers use `+manager<employee-ID>` aliases because `users.email` is unique.
These addresses all reach the same inbox, as explained by [Gmail alias help](https://support.google.com/mail/answer/22370).
Rerun the same command with the same inbox to preserve edits without duplicates.

Configure privately in `.env`, then recreate the backend:

```dotenv
EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=YOUR_GMAIL@gmail.com
SMTP_PASSWORD=<Gmail App Password>
EMAIL_FROM=YOUR_GMAIL@gmail.com
DEMO_EMAIL_TO=
```

Gmail App Passwords require [Google 2-Step Verification](https://support.google.com/accounts/answer/185833).
Keep the credential out of Git and chat. For `@example.com` fixture addresses,
set `DEMO_EMAIL_TO` to your own inbox; the adapter otherwise refuses delivery to
reserved example domains. With real Gmail aliases, leave it blank to retain the
individual envelope recipients. Addresses are hidden from one another in the
message headers. Gmail can consolidate deliveries to aliases of the same inbox.

[Akamai may restrict outbound SMTP on new accounts](https://techdocs.akamai.com/cloud-computing/docs/send-email).
The current instance timed out connecting to Gmail ports 465 and 587. A Gmail
App Password and working outbound SMTP are required before a real send can be
verified. Request SMTP access from Akamai support; no extra server is required.

Employee email delivery can be checked with **Check delivery status** in Message.
The UI shows actual destinations, any demo redirect, and SendGrid delivery activity.
HTTP 202 means accepted for delivery; mail-server delivery does not prove inbox
placement. Activity lookup never resends email. Keep `DEMO_EMAIL_TO` empty to send
to the selected employees' real demo Gmail aliases.

Automatic NORMAL recommendations have no notification audience. Repeated
non-normal recommendations keep affected on-site employees selected until their
notification is actually submitted. A prior evaluation or manager review email
is not an employee notification. Duplicate checks compare the latest employee
submission for the same action and a covering time window, per employee. Uncertain
submissions remain excluded until their provider outcome is resolved.
**Select affected staff** clears manual exclusions without changing the action.
Changing the action also clears manual exclusions and freshly selects all eligible
on-site staff for this decision's date/shift/window. An explicit return-to-normal
override can notify those staff too. Essential instructions still need confirmation;
managers can explicitly check remote/off-shift staff or choose Everyone at this
facility, including during NORMAL operations.

For later SMTP use, an explicit authenticated send API request after approval
is required. The server reloads the current
roster, checks approval/expiry/configuration and records delivery in the existing
`decisions.delivery_json` and `sent_at`. Direct duplicate submissions are blocked; recipients
already submitted the same action for a covering window are unchecked. The latest
submitted action is compared per employee, so a later different action can be
notified. A manager can deliberately approve and send again after a confirmation.
Prior completed deliveries, messages and recipient snapshots remain in
`alternatives_json.delivery_history`; review history records resend consent.
Pending or uncertain submissions still require resolution before retrying. Provider
acceptance is recorded honestly; inbox delivery is not confirmed by SMTP.

The review screen keeps sources, policy thresholds and uncertainty collapsed
until requested. The recipient list opens with all staff visible and editable.
Message has one **Approve and send email** button, which remains available after
a completed send and asks for confirmation before sending again.
Expired assessments offer **Refresh assessment**. For the hackathon, managers can
explicitly confirm a historical demo send; the email is labeled
`[HISTORICAL DEMO]` and retains the original dates. Expired operational notices
remain blocked without that confirmation.

## Scope and policy

- Exactly four tables: `locations`, `users`, `shifts`, `decisions`, plus the two
  specified explicit indexes. Schema is in `backend/schema.sql`.
- Seed: two offices, two managers, twelve staff, three weekly templates and mixed
  essential/remote flags. The additive demo expansion below adds five managed
  facilities, 80 staff and six shifts without deleting existing records.
- Two read-only `@tool` functions: `get_nws_weather` and `get_air_quality`. Requests
  have timeouts/backoff; NWS discovery URLs stay on its official API host.
  Open-Meteo/CAMS AQI is labeled modeled, not a verified sensor reading.
- One Strands adviser receives compact evidence and versioned demo policy. It
  generates posture, explanation, confidence, uncertainty and change conditions.
  The backend attaches original evidence and exact impact times, validates posture
  consistency and caps confidence. The model does not retype source JSON or
  timestamps, avoiding formatting/precision failures and unnecessary output.
  Malformed advice or unsupported posture still prevents saving a decision.
  The provider client allows one retry; the extra Strands retry loop is disabled
  so throttling cannot silently trigger six more model attempts. Authentication,
  quota, connectivity, timeout and output-limit failures have specific messages.
- `thresholds.py` labels all actions **app-defined demo policy**, anchored to
  [NWS documentation](https://www.weather.gov/documentation/services-web-api) and
  [AirNow AQI guidance](https://www.airnow.gov/aqi/aqi-basics/). AQI >=151 suggests
  telework; >=301 suggests closure. Qualifying severe alerts yield delayed opening,
  early dismissal or telework based on timing; immediate extreme alerts suggest
  closure. No trigger with weather coverage yields NORMAL. These actions are not
  government office-closure rules. Version 3 also suggests telework for actual
  forecast periods with sustained wind >=40 mph; this is an app-defined demo
  threshold. Forecast thunderstorms always add a dated manager-review warning
  and do not by themselves mandate an automatic schedule change.
- `demo-office-v3` retains every NWS alert in agent evidence and records why each
  does or does not trigger a schedule change. Overlapping advisories below the
  automatic trigger require manager review and cap confidence at 0.5. The
  dashboard and decision view show alert details, timing and NWS instructions.
  **NORMAL OPERATIONS means no automatic office schedule change, not hazard-free
  weather.** For example, a Rip Current Statement or High Surf Advisory remains
  visible without automatically closing an indoor office. Managers can edit the
  posture and choose facility-wide messages when local impacts require it.
- Every event returned by the NWS point alert feed is shown, without filtering
  by event name, severity, urgency, certainty or office hours. Unknown/new types,
  updates and test/canceled statuses remain visible with their source fields.
  The manager sees full alert descriptions/instructions directly from NWS,
  independently of model availability. Alert fetching survives forecast failure;
  a failed alert fetch is explicitly unavailable, never an empty all-clear.
  The dashboard retains previous alerts as potentially outdated during outages.
  New, revised and removed alerts flag review even if posture remains NORMAL.
  The agent only recommends; the manager chooses the action and message audience,
  edits text, approves or rejects, and chooses whether to send the saved message.
- Each decision retains original inputs, advice, recipients, draft and subsequent
  reviews in JSON. No extra audit or notification tables.
- APScheduler checks every three hours (`EVALUATION_INTERVAL_HOURS=3`) with
  `max_instances=1`, `coalesce=True`, one process lock and one Uvicorn worker.
  A minute tick evaluates only facilities whose saved last check is at least three
  hours old, or missing after creation/coordinate edits. Restarts catch up overdue
  work; busy checks retry on the next tick. Forced fresh startup checks are opt-in.
  Daily NORMAL-to-disruption transitions email assigned managers when enabled.
  `MANAGER_SCHEDULED_UPDATES_ENABLED=true` also sends manager summaries after each
  completed scheduled refresh, including unchanged and NORMAL statuses.
  Seven-day planning prepares future shift drafts; it does not
  change current status or automatically email anyone. The scheduler never sends
  employee emails.

## API

`POST /api/login` accepts `emp_id` and optional local `password`; use the returned
bearer token. Facility/decision routes check current manager role and assignment;
foreign/missing resources return 404. Tokens expire after eight hours. Public
mode requires a configured password and stable session secret.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Database connectivity |
| GET | `/api/locations/{id}/weather-alerts` | All current NWS alerts; independent of model/forecast |
| POST | `/api/login` | Manager session |
| GET / POST | `/api/locations` | Assigned facility / create and reuse current demo employee sheet |
| GET / PATCH | `/api/locations/{id}` | Status / edit ID and configuration |
| GET | `/api/locations/{id}/employees` | Staff and weekly schedules |
| POST | `/api/locations/{id}/evaluate` | Live sources + agent + snapshot |
| POST | `/api/locations/{id}/evaluate-week` | Seven local dates; one adviser call; separate affected shift drafts |
| GET | `/api/locations/{id}/week-plan` | Restore weekly overview and current draft recipients/review states |
| GET | `/api/locations/{id}/decisions` | History (`limit` 1–100, `offset` >=0) |
| GET | `/api/decisions/{id}` | Evidence/draft/review record |
| POST | `/api/decisions/{id}/recipients` | Fresh selection preview |
| POST | `/api/decisions/{id}/review` | Save/approve/reject with edited text |
| GET | `/api/me` | Current manager name, login ID and email; authenticated |
| GET | `/api/email-status` | Email provider configuration readiness; authenticated |
| POST | `/api/decisions/{id}/send` | Explicit delivery of the approved saved message |

The send route reloads staff, validates the saved approval and uses only the
selected database addresses. The request cannot provide arbitrary recipients.
Changed staff/address selection requires another approval. Messages can also be
downloaded through React.

## How Wednesday employees are selected

The model explains risk; it does not query employees or invent impact times.
`thresholds.py` calculates affected windows from API alert/forecast timestamps and
versioned policy. `agent.recommend_week()` explains the covered dates in one
Strands invocation; canonical windows and evidence are attached by the backend.
No employee identities or emails are sent to the model.

`recipients.load_roster()` executes this parameterized SQLite query:

```sql
SELECT u.*, s.name AS shift_name, s.weekly_hours_json
FROM users u
LEFT JOIN shifts s ON s.id = u.shift_id
WHERE u.location_id = ?
ORDER BY u.emp_id;
```

The facility query binds `(location_id,)`. A shift-specific draft suggests only
staff in that shift, but keeps the full facility roster visible so a manager can
manually include another shift, remote staff, or unscheduled employees.
The existing `idx_users_location` index restricts the roster before the join.
Python then converts the risk window to the facility timezone, picks the actual
weekday (`wed` on Wednesday), and reads that day's hours from the shift's JSON.
Automatic selection requires the following interval to have positive length:

```python
max(risk_start, shift_start, office_open) < min(risk_end, shift_end, office_close)
```

Default affected scope also excludes managers, already-remote staff, employees
without a valid scheduled shift, and NORMAL advice. Essential personnel remain
visible and require explicit manager confirmation. All include/exclude reasons
are saved with the decision. SQL chooses the roster; Python performs the dated,
timezone/DST-aware interval and eligibility checks. The LLM never writes SQL.

Complex model JSON is parsed and validated with `AgentAdvice` / `WeeklyAdvice`.
Dates must match the supplied covered dates, posture must match policy, and
canonical evidence/times are attached by the backend. SQL never compares the
entire JSON, searches the explanation for weekdays, or executes model-generated
SQL. Automatic filtering uses facility/shift IDs, validated posture and exact instants;
manual selections remain restricted to staff assigned to the manager's facility.
A malformed response or conflicting posture prevents saving decisions.

The weekly path loads the roster once and decodes each distinct shift once per
check, then reuses that data for all dates, shift drafts and saved recipient
counts. Each filter computes overlap once per distinct shift, rather than once
per employee. There is no global roster cache: manager previews/approvals reload
current staff in one indexed query. Separate selection windows are persisted in
existing decision JSON and reused by daily drafts and review, so a broad day
summary cannot accidentally select employees working only between hazards.
Overlapping/duplicate windows are merged; safe gaps remain separate. Existing
historical decisions without window metadata retain their stored impact bounds.

For example, a Wednesday 16:00–18:00 risk excludes an 08:00–16:00 shift and
includes an on-site 10:00–18:00 shift. A risk spanning both shifts creates two
independent subject/body templates and recipient lists. `weekly.py` splits
separate or mixed risk windows before grouping, so someone working only during
a safe gap is not selected. A person overlapping two separate windows can appear
in both drafts; these are separate messages, not duplicate sends.

**Check next 7 days** evaluates the current/next operating date plus six dates,
not necessarily Monday through Sunday. It fetches each of the two sources once.
[NWS hourly forecasts](https://www.weather.gov/documentation/services-web-api)
can cover seven days, while its active-alert feed contains currently issued
alerts, including any future effective periods. It cannot guarantee that all
future storm warnings have already been issued.
[Open-Meteo air-quality forecasts](https://open-meteo.com/en/docs/air-quality-api)
are requested for seven days; actual modeled coverage can be shorter. Missing
weather coverage is explicitly INSUFFICIENT DATA unless known source hazards
support a recommendation; missing AQI is a warning, never a fabricated reading.
A covered date without affected staff has a day assessment with zero default
recipients, allowing manager review or an explicit wider notice. Future drafts
do not overwrite today's facility status. Each shift/window has its own editable
text, recipient review and approval. The weekly overview survives navigation and
restart using JSON in the existing `decisions` table. No extra tables are added.

## Develop and test with uv

Python 3.12+ and `uv`; local React development requires Node 22.12+:

```sh
cd backend
uv sync --locked
uv run --env-file ../.env python seed.py
uv run --env-file ../.env uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open the Vite URL. Its `/api` proxy targets localhost:8000. Direct API docs are
at http://localhost:8000/docs in this mode. SQLite defaults to
`backend/data/facility.db`; Docker uses `/data/facility.db` on a named volume.

```sh
cd backend
uv run --locked pytest -q
```

Frontend interaction tests (synthetic data, no live model/source calls):

```sh
cd frontend
npm ci
npm test
npm run build
```

These exercise the week selector, three-step review, date-specific schedules,
exclusions, action changes, essential confirmation, exports, outage recovery,
location edits and unsaved navigation in a DOM environment. No browser visual
verification is implied.

Backend tests cover schema/seeds/constraints, auth/ownership, atomic ID edits, source
failures/retries, all five postures, invalid model advice, date/DST shift filtering,
essential review, fresh selections, approval/history, persistence and scheduling.
Fixtures are explicitly synthetic; live evaluation never uses them.

Runtime dependencies: FastAPI/Uvicorn, `httpx` for the two data sources,
`strands-agents[openai]` for the adviser, APScheduler for the in-process three-hour job.
Pytest is development-only. React and ReactDOM are the only browser runtime
packages. Vite builds the app; jsdom and esbuild also support frontend tests.
Both dependency lockfiles are included.

## Operations

```sh
docker compose ps
docker compose logs --tail=100 api web
docker compose restart api
docker compose down
```

The named volume survives restart and `down`. Avoid `down -v` when retaining
history. Never scale the API or run multiple workers with the embedded scheduler.
A concurrent evaluation/edit returns 409 and can be retried. Use password mode
and HTTPS before public exposure, as detailed in the Akamai runbook.
