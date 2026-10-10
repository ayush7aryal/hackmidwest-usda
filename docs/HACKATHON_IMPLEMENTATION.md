# Facility Operations AI — Hackathon Implementation Blueprint

## Goal
Build a working **manager-in-the-loop operational disruption assistant** hosted on **Akamai Cloud (Linode)**. Every hour it checks real US weather and air-quality signals for facilities and recommends **NORMAL, DELAYED_OPENING, EARLY_DISMISSAL, TELEWORK, or CLOSED**. A manager reviews evidence and affected employees, edits a drafted message and **manually** sends employee notifications. Never automatically announce a closure.

This is a hackathon MVP, not a production emergency-warning system. Prioritize a believable live demo, clear reasoning, safety and low complexity. Use real NWS data and synthetic facility/employee data. One facility type: **office**, one country: USA. Hazard coverage: **severe weather + poor air quality**. Optional road-data integration **only after the core workflow works**.

## Non-negotiable scope
- **4 database tables total**: `locations`, `users`, `shifts`, `decisions`. No alert, weather, email, prediction or audit sub-tables.
- **1 FastAPI backend**, **1 small React/Vite frontend**, **1 SQLite database** and **1 hourly job**. Docker Compose runs backend and frontend; the scheduled job runs inside the backend process for the hackathon. Use a persistent SQLite volume.
- **1 Strands agent** with 2 read-only custom tools: `get_nws_weather` and `get_air_quality`. No agent swarm, MCP server, Kafka, Redis, Celery, Kubernetes, vector DB, RAG, Spark or microservices.
- Use `uv` for Python dependencies. Start locally in Docker, then deploy the same Compose stack to an Akamai Compute Instance.
- The agent **advises only**. Backend validates output, applies clearly documented guardrails, selects impacted employees and controls notifications.
- No real employee emails by default: seed `@example.com` employees and use **mail sandbox / console delivery** for demos. Allow optional real SMTP only to explicitly configured and verified test recipients.

## Workflow

```text
Manager logs in (demo account)
       |
Creates a facility (name, latitude, longitude, timezone, open/close hours)
       |
Synthetic employees + weekly shifts are assigned to the facility
       |
[Hourly scheduler OR manager clicks "Evaluate now"]
       |
Fetch NWS forecast + active alerts AND air quality (real APIs)
       |
Strands agent analyzes signals + operational thresholds
       |
Backend validates structured recommendation & time window
       |
Store decision snapshot with source links, timestamps and reasoning
       |
If posture changed or affected window materially changed:
     update location current posture; email manager once (deduplicated)
       |
Manager opens decision: evidence, explanation, confidence, impacted staff
       |
Manager approves/edits posture or rationale and notification text
       |
Manager clicks "Send notifications" -> backend rechecks recipients
       |
Send only to affected eligible staff; persist outcome in decision record
```

**Important:** The hourly engine can recommend changes but cannot issue employee email without an explicit manager action. An employee's telework status excludes them from commute/arrival alerts. For a full closure or other safety-critical messages, the UI must show excluded remote staff clearly and allow a manager to choose an **all-staff notification**; do not silently suppress information they still need.

## Database — SQLite: exactly four tables

```sql
-- Facilities and their most recent generated recommendation.
CREATE TABLE locations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
    timezone TEXT NOT NULL,
    open_time TEXT NOT NULL,             -- Local HH:MM (same-day office hours)
    close_time TEXT NOT NULL,            -- Local HH:MM
    status TEXT NOT NULL DEFAULT 'NORMAL',
    status_reason TEXT,
    status_confidence REAL,
    affected_start TEXT,                 -- ISO timestamp with offset
    affected_end TEXT,
    last_checked_at TEXT,
    updated_at TEXT NOT NULL
);

-- A manager is a user with is_manager=1; all users belong to one facility.
CREATE TABLE users (
    emp_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    is_manager INTEGER NOT NULL DEFAULT 0 CHECK(is_manager IN (0,1)),
    is_essential INTEGER NOT NULL DEFAULT 0 CHECK(is_essential IN (0,1)),
    is_teleworking INTEGER NOT NULL DEFAULT 0 CHECK(is_teleworking IN (0,1)),
    shift_id INTEGER REFERENCES shifts(id)
);

-- One reusable weekly shift template; 7 days held in a small JSON object.
-- Example: {"mon":["08:00","16:00"],"tue":["08:00","16:00"],
--           "wed":null,"thu":["09:00","17:00"],"fri":["09:00","17:00"],
--           "sat":null,"sun":null}
CREATE TABLE shifts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    weekly_hours_json TEXT NOT NULL
);

-- Append-only evidence snapshots + review and delivery outcome in one record.
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    evaluated_at TEXT NOT NULL,
    posture TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    affected_start TEXT,
    affected_end TEXT,
    evidence_json TEXT NOT NULL,          -- source names, URLs, observed times, values
    threshold_json TEXT NOT NULL,         -- rule, threshold, actual value, margin
    alternatives_json TEXT NOT NULL,      -- uncertainty and what changes the recommendation
    recipients_json TEXT NOT NULL,        -- employee IDs, included/excluded, why
    draft_subject TEXT NOT NULL,
    draft_body TEXT NOT NULL,
    manager_status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING / APPROVED / REJECTED
    reviewed_by INTEGER REFERENCES users(emp_id),
    reviewed_at TEXT,
    sent_at TEXT,
    delivery_json TEXT,                   -- test recipient and per-recipient send result
    manager_alert_sent_at TEXT
);
```

**Implementation notes:** Create `shifts` before `users` when running DDL. Use migrations via a simple checked-in `schema.sql` for hackathon only. A manager is a row in `users`; don't add a separate employee table. `locations` stores the *latest* predicted posture; `decisions` retains the dated record. Store recipients as JSON because they're a frozen decision snapshot; query affected staff from current `users` + `shifts` at send time to prevent accidental stale delivery. Add only simple indexes on `users(location_id)` and `decisions(location_id, evaluated_at)`.

**Shift rule:** Parse weekly shift entries in the **facility's IANA timezone**, using `zoneinfo`. Cross-check both the facility's operating hours and each employee's scheduled local shift with `[affected_start, affected_end)` and the actual date. Support windows crossing midnight only if tested; for the MVP seed same-day shifts. Do not email unscheduled employees. Handle daylight-saving offsets correctly.

## Data collection: exactly two Strands tools

1. `get_nws_weather(latitude, longitude)`: call `https://api.weather.gov/points/{lat},{lon}` to discover grid endpoints; fetch forecast (prefer hourly) and `https://api.weather.gov/alerts/active?point={lat},{lon}` for active alerts. Include NWS `User-Agent` with contact info and descriptive app name, proper timeouts and retry/backoff. Extract relevant timestamps, forecast periods, hazards and severity/urgency/certainty from the **actual** API response. Preserve source URLs and fetch time. No hardcoded fake weather in live mode.
2. `get_air_quality(latitude, longitude)`: use a documented real AQI source with explicit configuration (e.g. Open-Meteo Air Quality API) and return AQI value, pollutant if available, measurement/forecast time, source URL and fetch timestamp. Record when unavailable. Treat modeled AQI as modeled, not a verified sensor reading.

Use `@tool` decorators from `strands` and call a **single** `Agent(model=..., tools=[...])`. Strands can use OpenAI via `strands.models.openai.OpenAIModel`; this works when the app itself is hosted on Akamai. Keep model configuration in `.env`, never in git. For Akamai-hosted inference use its documented compatible API endpoint only if your provisioned inference service supports this interface. Never let the agent execute SQL, write database records or send mail via tools.

**Source of truth:** `https://www.weather.gov/documentation/services-web-api`, `https://strandsagents.com/docs/user-guide/sdk/tools/`, `https://strandsagents.com/docs/user-guide/sdk/model-providers/openai/`.

## Decision logic — simple and auditable

- Preprocess live signals into compact JSON before giving them to the agent. Never pass enormous unfiltered forecasts.
- Maintain `thresholds.py`: **versioned, editable policy rules**. Prefer NWS severity/urgency/certainty and published AQI health guidance as defensible anchors. Record the exact rule and provenance in `threshold_json`. **Do not invent cutoffs as official government policy**. Clearly label any app-defined operational thresholds as demo policy.
- Agent returns strict JSON or validated Pydantic output:

```json
{
  "posture": "DELAYED_OPENING",
  "reason": "One paragraph explaining the actual risks and specific impact.",
  "confidence": 0.78,
  "affected_start": "2026-10-12T08:00:00-05:00",
  "affected_end": "2026-10-12T11:00:00-05:00",
  "evidence": [{"source": "NWS", "url": "https://api.weather.gov/...", "observed_at": "...", "value": "..."}],
  "thresholds": [{"rule": "...", "threshold": "...", "actual": "...", "margin": "..."}],
  "what_would_change": ["Alert expires", "AQI improves"],
  "uncertainty": "..."
}
```

- Validate allowed posture, `0 <= confidence <= 1`, timestamps, source references and that affected interval is plausible; a status with no valid evidence must not be labeled confidently actionable.
- Keep **confidence as an explained qualitative estimate**; never market the model's `0.78` as statistically calibrated probability.
- If NWS fails and air-quality data alone cannot justify a posture, show `INSUFFICIENT_DATA` in the evaluation/error UI **without overwriting the last known location status** or sending misleading notifications. This is a runtime evaluation state, not a sixth operational posture.
- Compare the new posture **and impact window** against the previous location state; only create a new manager alert when materially changed. Save a decision snapshot on each completed evaluation so history is reconstructable. Keep manager notifications deduplicated by decision ID; catch mail failures without losing the decision.
- Use straightforward operational policies: `NORMAL` = no disruption; `DELAYED_OPENING` = shift arrival moved; `EARLY_DISMISSAL` = departure moved; `TELEWORK` = eligible people work remotely (essential staff flagged for manager review); `CLOSED` = on-site operations suspended (manager must explicitly handle essential personnel).
- For evidence conflicts or vague time horizons prefer `PENDING` manager review rather than pretending certainty. All recommendations are subject to facility policy and human approval.

## Employee selection

At evaluation time, for each user assigned to the facility:
1. Ignore managers for ordinary staff notification unless explicitly selected.
2. Exclude users without a shift overlapping the impact window; save `excluded: not_scheduled`.
3. Exclude already teleworking users from **commute-specific** notices; save `excluded: already_remote`.
4. Include overlapping employees and annotate `is_essential` as **requires manager review** (never silently instruct critical staff to stay home).
5. Show **every include/exclude reason** in frontend. Manager can include/exclude specific employees before send; backend validates they belong to that facility.
6. Manager must be able to choose wider facility-wide messages separately when needed (e.g. closure applies to remote staff too).
7. On Send, reload staff records + shifts, check role/authorization, ensure there is an approved decision and ignore duplicate send clicks using `sent_at` and a transaction. Store send results in `delivery_json`.

## Notifications

- **Automatic to manager only:** subject `Review needed: {facility} — {posture}` with risk summary and a link to the decision UI. Only on material status/window change. If manager email delivery fails, show a dashboard warning.
- **Manual to employees:** email subject/body generated from decision but fully editable by manager. Include facility, effective local date/time with timezone, current recommended action, reason, manager contact and whether the instruction is approved. Never present a draft recommendation as a finalized emergency directive.
- Use mail sandbox or console adapter by default; production SMTP is optional. Log test delivery results in same decision. Do not send to public real emails in demo.

## Frontend — simple React/Vite dashboard

**Pages/views (only 3):**

1. **Login**: select seeded manager demo account, or use minimal configured password login if deployed publicly. Demo-only selector cannot be used on a public internet deployment without authentication. No employee self-service portal.
2. **Facilities/dashboard**: cards showing facility name, last evaluation time, current recommendation, impact window, confidence, manager-review badge. Button `Add facility` opens form for `name, lat, lng, timezone, open_time, close_time`. Button `Evaluate now`. Small list of most recent decisions.
3. **Decision details**: recommended posture, effective shift window, reason, source links & timestamps, threshold crossed and margin, confidence/uncertainty, what would change recommendation. Table of **affected/filtered employees**: name, shift, essential, teleworking, included?, exclusion reason and selectable checkbox. Editable subject/body. Buttons `Approve`, `Reject`, `Send notifications` (send disabled until approved). Status for manager email and employee delivery. Show historic decisions for audit.

No charts, maps, chatbots, staff CRUD suite, admin panel or advanced permissions unless spare time remains. Use simple responsive CSS/Tailwind; prioritize clear status labeling over styling.

## API endpoints

```text
POST /api/login                    # Demo/session login (enforce auth for public demo)
GET  /api/locations                # Manager-owned facilities
POST /api/locations                # Create facility
GET  /api/locations/{id}           # Current status
POST /api/locations/{id}/evaluate  # Manually run one real evaluation
GET  /api/locations/{id}/decisions # History
GET  /api/decisions/{id}           # Evidence, recipients, draft
POST /api/decisions/{id}/review    # Approve/reject, edited posture/message
POST /api/decisions/{id}/send      # One-time manual email action
GET  /api/health                   # Basic health check
```

Use FastAPI/Pydantic response schemas. Enforce manager `location_id` access on *every* route. Do not expose arbitrary mail recipients in send requests.

## Hourly execution + deployment

- During a hackathon run **APScheduler** in the **single-worker** FastAPI container on a 60-minute cadence, with `max_instances=1`, `coalesce=True` and a lock to prevent concurrent manual and scheduled evaluation of the same facility. Run a check at application startup only if explicitly enabled to avoid surprise duplicate requests. **Do not run multiple Uvicorn/Gunicorn workers with embedded scheduler.**
- `docker-compose.yml`: `api` service (FastAPI + SQLite volume at `/data`) and `web` service (static frontend served through Nginx; proxy `/api` to backend). For a very small demo these are the only two services.
- In local development use `uv sync`, `uv run`, `.env.example` and Compose; use `docker compose up --build` to validate the full stack.
- On Akamai: create a small Linux Compute Instance, install Docker Engine + Compose, clone repo, set secrets in `.env`, mount SQLite persistent path and run `docker compose up -d --build`. Use firewall, HTTPS via reverse proxy or managed ingress and a real manager password before internet exposure. **All compute/application processing lives on Akamai**; external NWS/AQI/model/mail APIs are upstream services, not alternative application hosts.
- Keep deployment, seed and test instructions in README. Do not add managed databases or Kubernetes for this demo.

## Suggested repository structure

```text
project/
  backend/
    app/
      main.py              # FastAPI routes + lifespan
      db.py                # SQLite connection, schema setup, queries
      models.py            # Request/response and agent output validation
      agent.py             # One Strands agent and its prompt
      tools.py             # NWS and AQI @tool functions
      evaluator.py         # Fetch -> agent -> validate -> store
      thresholds.py        # Traceable demo operating policy
      recipients.py        # Timezone-safe shift overlap filtering
      mailer.py            # Sandbox output + optional test SMTP
      scheduler.py         # Once-per-hour scheduler
    schema.sql
    seed.py                # Synthetic manager, facilities, shifts, employees
    pyproject.toml
    uv.lock
    Dockerfile
  frontend/
    src/
      App.jsx              # 3 views with minimal components
      api.js
      style.css
    package.json
    Dockerfile
    nginx.conf
  docker-compose.yml
  .env.example
  .gitignore
  README.md
```

## Implement in this order (coding agent: complete each before the next)

1. **Persistence + seed**: implement exactly four tables and create ~2 facilities, 2 managers, 12 synthetic staff, 3 shift templates. Prove employees map to facilities/shifts and flags work.
2. **Live sources**: implement/test both API tools. Confirm that NWS sources truly come from `api.weather.gov` and AQI from configured provider. Show sensible unavailable-data handling.
3. **Agent + policy**: instantiate one Strands agent, produce schema-validated decision, store evidence + thresholds + alternatives, handle insufficient data. No fabricated source citations.
4. **Recipient matching**: test overlap/non-overlap, teleworking exclusions, essential warnings, manager exclusions, midnight/day boundaries and facility ownership.
5. **Review + mail**: implement pending approval, editable draft and one-time manual sandbox send; simulate failure and verify clear UI/history.
6. **Frontend**: build the three views and connect API; finish the end-to-end click path first.
7. **Hourly scheduler + Docker**: check persistence after restart and no duplicated runs or email sends.
8. **Akamai deployment**: run the same Compose file on Akamai and verify `/api/health`, real API fetch, review flow and test delivery.

## Hackathon acceptance tests / live demo script

- Create a new facility with US coordinates; evaluation fetches **live NWS data** and AQI and displays **source URLs, timestamps and current evidence**.
- For a clear hazard case, select a *real location with an active alert*, or use a clearly labeled replay fixture **only to show a particular posture/shift overlap**. Never describe a fixture as live data.
- Show all five recommendation categories available in policy, meaningful graded advice and why a specific threshold was crossed.
- Prove schedule overlap includes only on-site affected employees and explain exclusions; show essential personnel review.
- Manager receives **review request on change** and employee mail is never automatically sent; manager edits and approves the draft then clicks send.
- Show decision history containing original inputs, time, threshold rationale, recommendation, recipients, reviewer, edits and delivery results.
- Restart Docker and verify persisted decisions and no duplicate employee sends.

## Coding-agent working rules

- **Do not add tables, services, packages or abstractions without explaining why existing four tables and two containers cannot meet the requirement.** Make targeted changes in the current module rather than inventing frameworks.
- Small readable Python functions, type annotations, Pydantic validation, parameterized SQL, timeouts for external I/O and actionable error messages.
- Include short explanatory comments for non-obvious logic and every important function. Avoid noisy comments that restate trivial code.
- Use `uv` and pinned lockfile. No speculative APIs. Follow current Strands docs; test package imports before coding integration.
- Test locally using seeded data; document commands and relevant logs. On failure print the exact failing test/log and repair it before adding new features.
- No full production IAM/audit/auth architecture; do protect public demo login, secrets and mail sending.
- Prefer complete end-to-end working vertical slices over extra features or visual polish.

## What is deliberately NOT built

Employee portal, flexible per-employee overrides, calendar integrations, multi-tenant enterprise authentication, external message queue, high availability, multi-agent orchestration, asynchronous task farm, GIS map, ML training pipeline, road-condition integrations, separate raw-data storage and six extra database tables. These can come **after** the hackathon.
