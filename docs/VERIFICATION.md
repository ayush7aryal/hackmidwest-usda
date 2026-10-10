# MVP verification — October 10, 2026

Completed locally before packaging for Akamai:

- `cd backend && uv run --locked pytest -q`: **201 passed**.
- `cd frontend && npm ci && npm run build`: production build passed.
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

No Akamai instance has been provisioned or remotely tested. Ubuntu installation,
public DNS and Certbot issuance/renewal must be verified on the target instance
using [the deployment runbook](AKAMAI_DEPLOYMENT.md). Browser click-through and
visual verification remain unverified; the checks above exercised HTTP APIs,
served frontend assets and the production build.
