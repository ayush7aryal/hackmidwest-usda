# MVP verification — October 10, 2026

SendGrid integration update:

- Installed the official `sendgrid==6.12.5` SDK through uv and updated the lockfile.
  `EMAIL_PROVIDER=sendgrid` selects the HTTPS adapter; SMTP remains optional.
- **251 backend tests passed**, including 16 new SendGrid cases. Tests run the
  actual SDK with a mocked HTTP transport and verify POST endpoint, authentication,
  timeout, private recipient envelopes, approval/duplicate guards, provider ID
  persistence, client rejections and uncertain delivery handling. No real emails
  are sent by these tests.
- Email remains disabled and the judging frontend remains unchanged. See
  [SendGrid setup](SENDGRID_SETUP.md).
- A read-only request from Akamai reached SendGrid over HTTPS and received HTTP
  405, confirming network connectivity without authentication or email submission.
- After private key/sender configuration, the actual SendGrid SDK submitted one
  clearly labeled setup test from Akamai to the owner's `+1` Gmail alias and
  received **HTTP 202 ACCEPTED** with a provider message ID. This verifies provider
  authentication and request acceptance, not inbox receipt. The isolated setup
  process did not approve or send any operational decision. The main application
  retains `EMAIL_ENABLED=false`; credentials were securely synchronized, the
  services recreated and public HTTPS health/manager login rechecked successfully.

Judging demo update: email setup/status notices and send controls were removed
from the frontend. It no longer requests `/api/email-status` or `/send`.
Message editing, approval, recipient filtering and download remain available.
Email delivery is disabled in the local and Akamai demo configuration; the
optional backend implementation and private credentials are retained for later.
All **23 frontend DOM tests** and the production build passed after this change.
Earlier email checks below describe the previous version.

Five-office demo and explicit email update (October 10, Chicago time):

- **235 backend tests** and **23 frontend DOM interaction tests** passed. The
  production frontend build passed. Added coverage checks additive/idempotent
  seeding, ownership, all-day/weekend/holiday shift matching, NORMAL/unchanged
  suppression, manager overrides, duplicate recipients, stale approvals,
  partial/uncertain delivery and retry behavior. SMTP unit tests use a fake
  transport; they do not establish real delivery.
- Backed up the live database before the additive migration to
  `/root/facility-backups/before-demo-20261011T000548Z.db`. Existing facilities,
  manager assignments and 100 decisions were preserved. Five additional managers
  and offices, 80 fictional staff and six shift templates were added. The app
  retains exactly four tables and its original one-agent/two-hazard-tool design.
- Public trusted HTTPS checks passed for all seven managers and facility
  isolation. Each new manager sees one office and 16 employees. All 104 synthetic
  staff have unique numbered aliases of the configured Gmail inbox. These are
  aliases to one real inbox, not separate mailboxes.
- Five live evaluations saved decisions #101–105 using live weather/AQI sources
  and the configured adviser. Jacksonville returned TELEWORK with two recipients;
  Panama City Beach, Charleston, Savannah and Tampa returned NORMAL with zero
  checked recipients. CLOSED override previews selected the two scheduled onsite
  staff per office, flagged one essential employee, and excluded remote staff.
  A second Jacksonville evaluation (#106) again returned TELEWORK and correctly
  selected zero recipients with `unchanged_operation` suppression.
  These are time-specific observations; a location cannot guarantee severe weather
  during a later demo. Advisories remain visible even when operations are NORMAL.
- The deployed scheduler remains enabled every 10,800 seconds and reloads all
  saved locations, including the five additions. Tests check new-location
  inclusion; no new full elapsed three-hour cycle is claimed by this update.
- Final cloud checks found 13 locations, 111 users, nine shifts and 106 decisions;
  integrity/foreign-key checks passed. Exactly `api` and `web` are healthy.
  Both Docker images were also rebuilt locally and the additive seed applied
  after backing up the local database. All five new local logins passed; local
  history was preserved. Local and cloud database volumes remain separate.
- Gmail accepted **one setup test** from the local computer through the actual
  application's SMTP adapter to the owner's `+1` alias. No operational decision
  was approved or sent by this test; inbox receipt is not independently verified.
  The Gmail App Password stays in ignored/private configuration files. Connections
  from Akamai to Gmail SMTP ports 587 and 465 still timed out after the user
  requested access. Cloud email delivery remains blocked by outbound connectivity.
  Approval and explicit sending remain separate manager actions.

Earlier deployment and implementation checks follow; their counts and outcomes
describe the snapshots verified at that time.

Akamai deployment verified on October 10, 2026:

- Live app: **https://172.236.119.150**. Instance `107866813` is running in Chicago
  (`us-ord`) on Ubuntu 24.04 with the approved 2 GB / 1 vCPU / 50 GB plan.
  Firewall `201494741` exposes 80/443 and limits SSH to the deployment IP.
  The backend has no published port; exactly `api` and `web` remain running.
- Source archive and database checksums matched after secure transfer. The
  existing database was imported without seeding: 7 facilities, 26 users,
  3 shifts and 91 decisions. Four-table, integrity and foreign-key checks passed.
- The deployed scheduler's actual batch function evaluated all 7 facilities
  against live NWS alerts/forecasts, modeled Open-Meteo AQI and the configured
  Akamai `hackathon-chat` Strands adviser. All facilities updated successfully
  in 95.48 seconds; decisions #92–98 were saved PENDING, bringing the total to
  98. Both source tools were available for all seven evaluations. No delivery
  timestamps were set, and no messages were sent.
- Runtime configuration enables the 10,800-second interval with one worker,
  `max_instances=1`, `coalesce=True`, and no startup evaluation. Newly added
  facilities are selected on the next batch. The manual live batch above verifies
  the job function; a full elapsed three-hour cycle has not yet been observed.
  Current daily status is scheduled; seven-day plans remain manager-triggered.
- Let's Encrypt issued a trusted IP-address certificate with the correct IP SAN,
  valid through October 17, 2026 at 14:03:04 UTC. Certbot 5.8.0 runs on the host.
  `facility-cert-renew.timer` is enabled/active and checks twice daily.
  `certbot renew --dry-run --run-deploy-hooks` passed, including stopping and
  restoring the web container. Trusted HTTPS checks passed again afterward.
- Public HTTP redirects to HTTPS. Frontend HTML/JS/CSS, health, both manager
  logins, missing/wrong-password rejection, facility ownership and staff counts
  passed through the public HTTPS URL with normal certificate verification.
  Manager 1001's previously saved weekly plan was preserved.
- The existing authenticated session, facility details, roster, decisions and
  weekly plan survived an API restart. Production password/config files are
  mode 600 and excluded from Git; password SSH login is disabled.

Completed locally before packaging for Akamai:

- Scheduled refresh now defaults to **three hours** via
  `EVALUATION_INTERVAL_HOURS=3`. Tests verify 10,800-second intervals, rejection
  of invalid settings, no overlapping runs and no default startup check. An
  integration test adds a facility through the API, runs the scheduler with
  synthetic hazard/model responses and verifies its current status, timestamp
  and pending decision are persisted without email delivery. Facility IDs are
  reloaded on every run; seven-day plans remain manager-triggered.

- Latest update: approved bodies/exports omit draft and reviewer labels, including
  legacy saved approvals, while original drafts and review history remain intact.
  Essential instructions and manager edits are preserved. Risk contains the
  emphasized operational action; Message focuses on wording and audience.
- Add/edit facility includes a Leaflet map with click/drag pin selection, explicit
  US city/ZIP search (Open-Meteo/GeoNames), local coordinate search, separate
  facility name and the existing ID/coordinate/timezone/hour fields. DOM tests
  exercise actual Leaflet click handlers, place-result selection, invalid
  coordinates, empty results and search outages. No map key or third agent tool.
- Concurrent source reads and compact model input retain all canonical evidence,
  per-date alert assessments and exact selection windows. A barrier test proves
  the requests overlap and each evaluation refetches sources. The agent keeps
  one invocation for seven dates and two hazard tools. Hidden model HTTP retries
  are disabled so a failed attempt returns for an explicit manager retry.
- One live seven-day advisory check on the configured facility and Akamai model
  validated all seven dates in **31.55 seconds** (sources **1.06 seconds**, model
  **30.49 seconds**). Input size fell from 34,047 to 14,887 characters for that
  snapshot. This read-only timing check made no database writes or sends. The
  earlier 50.7-second check below used an older snapshot; these are individual
  observations, not a controlled benchmark or latency guarantee.
- Evaluation has an accessible spinner, elapsed time, slow-response text and
  duplicate-request protection; DOM tests verify loading and completion states.
  Browser visual/map-tile interaction is not verified: no browser is available
  to the automation connector. Production build and served assets are checked.
- Rebuilt and recreated both Docker services with the existing SQLite volume.
  Authenticated API checks retained 20 recent decisions and verified one existing
  approved message is returned clean. All four tables, integrity and foreign-key
  checks passed. Nginx serves `/assets/index-fOMqrRXE.js` and
  `/assets/index-BkYgZ6ef.css`, including map styles and loader. Its referrer policy
  permits attributed OSM tile requests. Live Panama City Beach place search
  returned valid coordinates and timezone. No facility/staff/approval was changed
  during these final checks.

- `cd backend && uv run --locked pytest -q`: **213 passed**.
- `cd frontend && npm ci && npm run build`: production build passed.
- `cd frontend && npm test`: **19 DOM interaction tests passed** for the compact
  week selector and Risk → Recipients → Message flow. Tests cover one date shown
  at a time, mixed shift actions, expandable source notes/advisories, visible
  urgent instructions, visible test/canceled/unknown statuses, dated shift hours,
  location ID/coordinate edits, recipient
  exclusions, action changes, essential confirmation, saved exports with actual
  recipients, model/alert/recipient outages and unsaved navigation protection.
  Reading steps does not mark unchanged drafts dirty; failed refreshes preserve
  the previous plan/alerts with explicit warnings. These use synthetic HTTP
  responses and a DOM environment, not a browser or live model.
- The prior compact-layout update moves policy/evidence, daily details, history and decision records
  behind disclosures. One selected date and one review step are shown at a time.
  No backend schema, policy, tool, recipient or approval rules changed.
- Live NWS + Open-Meteo + OpenAI/Strands evaluation through Nginx saved a
  `NORMAL` decision, real source evidence, filtered staff and a draft for review.
  The decision remains `PENDING`; delivery timestamps are empty.
- After switching to the supplied Akamai workshop team key and endpoint,
  authenticated model discovery returned `hackathon-chat`, `hackathon-embedding`
  and `hackathon-image`. A complete NWS/AQI/Strands evaluation using
  `hackathon-chat`, JSON schema output and the 4,096-token limit saved decision
  #3 with `NORMAL` posture, a pending draft and zero affected recipients.
  No notifications were sent. The 107-test suite passed after this integration.
- Panama regression checked live at `30.1766,-85.8055`: decision #7 retains the
  Moderate Rip Current Statement and Minor High Surf Advisory, with full NWS
  descriptions/instructions, two review warnings and confidence capped at 0.5.
  The agent explicitly names both hazards. `NORMAL` means no automatic office
  schedule change under `demo-office-v2`, not hazard-free weather. The decision
  remains `PENDING`, and both delivery timestamps are empty.
- The rebuilt frontend serves `NORMAL OPERATIONS` and alert details in the
  dashboard and decision view. React server-render checks verified advisory
  instructions, manager-review text, absent alerts and malformed alert times.
  Regression tests cover below-threshold advisories, timing, canceled/test
  alerts and the real Strands adapter receiving alert instructions.
- All-alert manager flow verified with synthetic tornado, flood, hurricane,
  winter, heat, fire, surf, air-quality and unrecognized event fixtures. Alerts
  remain visible regardless of severity or office-hour overlap. Independent
  alert-only API tests cover authentication/ownership, model independence,
  forecast outage, explicit source failure, and zero decision/status mutations.
  New, updated and removed alerts trigger review with an unchanged NORMAL
  posture; identical alerts in a different order do not. All five manager action
  overrides preserve original advice and leave delivery timestamps empty.
- React render checks cover unknown categories/fields, missing instructions,
  invalid timing, empty results, source outages and potentially outdated alerts.
  The dashboard polls the alert feed every minute while open; instructions are
  visible without expanding the full description. Build checks passed.
- Model-copy regression fixed: the adviser generates validated `AgentAdvice`;
  the backend constructs the full recommendation using original source evidence
  and precise impact timestamps. Tests exercise the real Strands adapter with
  streaming and non-streaming responses, multiline evidence and microseconds,
  forbidden replacement evidence/times, malformed JSON, HTTP 400/401/403/429/500,
  network/timeouts and token/context limits. Throttling tests confirm there is
  no nested six-attempt Strands retry loop; the HTTP client retains one retry.
- Live workshop evaluation of Panama after this fix saved decision #9 in 13.4
  seconds with both alerts and exact canonical evidence, `PENDING` manager review
  and empty delivery timestamps. A pre-change evaluation (#8) took 66.7 seconds;
  these are individual checks, not a latency guarantee. Both containers healthy.
- New demo locations reuse the manager's existing employee sheet without
  regenerating identities or duplicating users. Repeated-create tests preserve
  IDs, names, emails, shift assignments and essential/remote flags, maintain
  foreign-key integrity and leave the other office's roster unchanged. Synthetic
  end-to-end API tests cover creation and evaluation for all five postures in
  the new location's timezone, including the shorter early-dismissal recipient
  window, pending manager review and empty delivery timestamps.
- Seven-day planning tested with explicitly synthetic Wednesday storm/wind
  fixtures. Separate dated early/late shift messages contain only their eligible
  on-site staff; exact shift-end boundaries, disjoint hazards with a safe gap,
  future wind forecasts, missing coverage and absent sources are covered. The
  weekly API fetches the same two tools once and invokes one Strands adviser for
  all covered dates. Real SDK streaming/non-streaming tests validate seven dates,
  original impact windows, confidence caps and the two-tool schema in one call.
  Invalid/duplicate dates, conflicting model postures and model outages save no
  partial drafts or current status changes. SQL still uses the four-table schema.
- Weekly draft previews enforce the specific shift, reject foreign-shift IDs,
  preserve essential-personnel confirmation, and restore current manager edits,
  recipient counts and review states. Facility configuration changes invalidate
  the saved overview. Forecast tools retain seven-day data instead of truncating
  it to 48 hours/three days. Wind parsing rejects unsupported units and gust text.
- React server-render checks passed for Wednesday labels, two shift links and
  recipient lists, approval labels, missing coverage and no-recipient day review.
  Both Docker images built successfully and both services became healthy.
- Live weekly evaluation through Nginx and the configured Akamai workshop
  `hackathon-chat` endpoint completed in 50.7 seconds for October 10–16. It saved
  two October 12 affected-shift drafts (decision #14 and #15), each with two
  recipients. October 14 had no automatic disruption trigger in this live
  snapshot; Wednesday storm tests above are synthetic, not live predictions.
  October 15–16 explicitly warned of incomplete modeled AQI coverage. The saved
  overview restored correctly, today's facility status/check timestamp stayed
  unchanged, and all saved assessments/drafts remained PENDING with both email
  delivery timestamps empty. This single live timing is not a latency guarantee.
- Efficient response-to-roster filtering verified: a complete weekly evaluation
  performs one employee/shift join, decodes each distinct shift once, and saves
  recipient counts from the same filtered rows. SQLite query-plan checks confirm
  `idx_users_location` is used. Manager previews reload current staff; no global
  roster cache or new table/index is introduced. Mixed overlapping hazards use
  the strongest action and keep disjoint safe gaps separate.
- A synthetic end-to-end test passes complex, reordered/pretty-printed agent JSON
  through the real Pydantic parsing/validation boundary. SQL-like text, another
  weekday and a foreign employee ID inside the explanation do not affect the
  audience. Daily selection, recipient preview and saved review all exclude an
  employee scheduled only during the safe gap between two hazards. Equivalent
  UTC/local offsets yield identical results; malformed interval/schedule shapes
  are rejected or explicitly excluded. Existing DST, essential-review and
  cross-facility guards continue to pass.
- Rebuilt/restarted the updated API container and checked authenticated recipient
  previews through Nginx for saved shift decisions #14 and #15: two recipients
  each, correct facility/shift ownership and essential-review flags, and no
  mutation of either saved decision. No extra model invocation or email delivery.
- Akamai Compose override checked: exactly `api` and `web`, password login
  enforced, no published backend port, persistent SQLite volume.
- Isolated public-mode stack tested with the HTTPS Nginx configuration and a
  temporary localhost certificate explicitly trusted by the HTTP test client.
  HTTP redirected to HTTPS. TLS certificate verification remained enabled.
- HTTPS checks passed for frontend delivery, missing/wrong-password rejection,
  successful manager login, cross-facility rejection, and editing location ID,
  name, latitude and longitude.
- A **synthetic deployment-test decision** verified employee selection, explicit
  essential-staff confirmation, edited draft approval and saved review history.
  No source readings were fabricated or presented as live in this test.
- The same session, facility edits and approved decision survived API restart.
  SQLite integrity, foreign keys, exactly four tables and a consistent backup
  were checked. All email delivery timestamps remained empty.
- The isolated test containers, volume and network were removed afterward;
  the ordinary local demo stack and its live decision were preserved.

Operator commands are in [the deployment runbook](AKAMAI_DEPLOYMENT.md).
Browser click-through and visual verification remain unverified because no
browser is available to the automation connector. The checks above exercised
HTTP APIs, served frontend assets, DOM interactions and the production build.
