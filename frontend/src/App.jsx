import React, { useEffect, useState } from 'react'
import { api } from './api.js'

const POSTURES = ['NORMAL', 'DELAYED_OPENING', 'EARLY_DISMISSAL', 'TELEWORK', 'CLOSED']
const label = value => value === 'NORMAL' ? 'NORMAL OPERATIONS' : value?.replaceAll('_', ' ') || '—'
const when = (value, timezone) => {
  if (!value) return 'Not provided'
  const instant = new Date(value)
  if (!Number.isFinite(instant.getTime())) return 'Time unavailable · check NWS source'
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium', timeStyle: 'short', ...(timezone ? { timeZone: timezone } : {}),
  }).format(instant)
}
const decisionFromHash = () => window.location.hash.match(/^#decision\/(\d+)$/)?.[1] || null

function Badge({ children, tone = '' }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

function ErrorMessage({ children }) {
  return children ? <div className="notice error" role="alert">{children}</div> : null
}

function WeatherAlerts({ alerts = [], timezone, current = false, available = true, fetchedAt, sourceUrl, error, onRefresh, refreshing }) {
  if (!current && !alerts.length) return null
  return <aside className="weather-alerts" aria-label="NWS weather alerts" role={alerts.length || !available ? 'alert' : 'status'}>
    <div className="section-head"><h3>{current ? 'Current NWS alerts' : `${alerts.length} NWS alert${alerts.length === 1 ? '' : 's'} at this evaluation`} · manager review</h3>
      {onRefresh && <button disabled={refreshing} onClick={onRefresh}>{refreshing ? 'Checking alerts…' : 'Refresh alerts'}</button>}</div>
    {current && <p className="help">{fetchedAt ? `Checked ${when(fetchedAt, timezone)}. ` : ''}Alerts refresh every minute while this dashboard is open.</p>}
    {!available && <p className="warning-text">{error || 'NWS alerts are unavailable.'} Cannot confirm current alert conditions. {alerts.length ? 'The alerts below are from the last successful check and may be outdated.' : 'Check the NWS source directly.'}</p>}
    {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">Open NWS alert feed ↗</a>}
    {available && !alerts.length && <p>NWS returned no active alerts for these coordinates. This does not guarantee hazard-free conditions.</p>}
    {!!alerts.length && <p>Review every alert below, including advisories and alerts outside office hours. You decide whether to change operations, prepare a safety message, or take no action. Normal operations means no automatic office schedule change.</p>}
    {alerts.map((alert, index) => <article key={alert.url || index}>
      <a href={alert.url} target="_blank" rel="noreferrer"><strong>{alert.event || 'NWS alert (type not provided)'} ↗</strong></a>
      <p className="help">{alert.severity || 'Unknown'} severity · {alert.urgency || 'Unknown urgency'} · {alert.certainty || 'Unknown certainty'}{alert.areaDesc ? ` · ${alert.areaDesc}` : ''}</p>
      {(alert.status || alert.messageType) && <p className="help">{alert.status || 'Unknown status'} · {alert.messageType || 'Unknown message type'}{alert.response ? ` · NWS response: ${alert.response}` : ''}</p>}
      <p className="help">{when(alert.onset || alert.effective, timezone)} – {when(alert.ends || alert.expires, timezone)} {timezone}</p>
      {alert.headline && <p><strong>{alert.headline}</strong></p>}
      <p className="alert-description"><strong>NWS instructions: </strong>{alert.instruction || 'No separate instructions were provided. Read the full alert and NWS source.'}</p>
      <details><summary>Read full alert message</summary>
        <p className="alert-description">{alert.description || 'No description provided. Open the NWS source for details.'}</p>
      </details>
    </article>)}
    {current && <p className="help">These source messages are shown independently of the agent. No action or email is automatic.</p>}
  </aside>
}

function Login({ onLogin }) {
  const [employee, setEmployee] = useState('1001')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const result = await api('/login', { method: 'POST', body: { emp_id: Number(employee), password } })
      sessionStorage.setItem('facility_token', result.access_token)
      sessionStorage.setItem('facility_manager', result.manager.name)
      onLogin()
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  return <main className="login-layout">
    <section className="login-intro"><p className="eyebrow">FACILITY OPERATIONS AI</p>
      <h1>Clear evidence.<br />Considered decisions.</h1>
      <p>Weather and air quality can change the working day. Review the risks, check your team’s schedules, and prepare the right message.</p>
      <div className="login-principle"><span>01</span><p>Live signals from NWS and Open-Meteo</p></div>
      <div className="login-principle"><span>02</span><p>Advice with a manager in control</p></div>
      <div className="login-principle"><span>03</span><p>Messages prepared. Email delivery off.</p></div>
    </section>
    <section className="card login-card"><p className="eyebrow">MANAGER ACCESS</p><h2>Welcome back</h2>
      <p className="muted">Choose your seeded manager account.</p>
      <form onSubmit={submit}><label>Manager account<select value={employee} onChange={e => setEmployee(e.target.value)}>
        <option value="1001">Alex Morgan · Chicago</option><option value="2001">Jordan Lee · Denver</option>
      </select></label>
      <label>Manager password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter the configured manager password" /></label>
      <p className="help">A configured password is required even in local demo mode. Leave blank only if no password is configured.</p><ErrorMessage>{error}</ErrorMessage>
      <button className="primary full" disabled={busy}>{busy ? 'Signing in…' : 'Open dashboard →'}</button></form>
      <p className="help login-foot">Synthetic employees · US offices · Human review required</p>
    </section>
  </main>
}

function FacilityForm({ facility, onSaved, onCancel }) {
  const [values, setValues] = useState(facility || { name: '', latitude: '', longitude: '', timezone: 'America/Chicago', open_time: '08:00', close_time: '18:00', id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fields = [ ['id', 'Location ID', 'number'], ['name', 'Facility name', 'text'],
    ['latitude', 'Latitude', 'number'], ['longitude', 'Longitude', 'number'],
    ['timezone', 'IANA timezone', 'text'], ['open_time', 'Opens (local)', 'time'], ['close_time', 'Closes (local)', 'time'] ]
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('')
    const body = Object.fromEntries(fields.map(([key]) => [key, values[key]]))
    body.latitude = Number(body.latitude); body.longitude = Number(body.longitude)
    if (body.id === '') delete body.id
    else body.id = Number(body.id)
    try {
      await api(facility ? `/locations/${facility.id}` : '/locations', { method: facility ? 'PATCH' : 'POST', body })
      onSaved()
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  return <section className="card"><div className="section-head"><h2>{facility ? 'Edit facility' : 'Add facility'}</h2><button onClick={onCancel} disabled={busy}>Cancel</button></div>
    <p className="muted">{facility ? 'Changing the location ID preserves staff and history. Configuration changes require a fresh evaluation.' : 'For this demo, your current employee sheet moves with you: the same employee IDs, names, emails, shifts and flags are reused at the new location. Run a fresh evaluation to filter recipients.'}</p>
    <form onSubmit={submit}><div className="form-grid">{fields.map(([key, title, type]) => <label key={key}>{title}
      <input type={type} value={values[key]} required={key !== 'id'}
        min={key === 'id' ? 1 : key === 'latitude' ? -90 : key === 'longitude' ? -180 : undefined}
        max={key === 'latitude' ? 90 : key === 'longitude' ? 180 : undefined}
        step={['latitude', 'longitude'].includes(key) ? 'any' : undefined}
        placeholder={key === 'id' ? 'Automatic if blank' : key === 'timezone' ? 'America/Chicago' : undefined}
        onChange={e => setValues({ ...values, [key]: e.target.value })} />
    </label>)}</div><ErrorMessage>{error}</ErrorMessage><button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save facility'}</button></form>
  </section>
}

function Dashboard() {
  const [facility, setFacility] = useState(null)
  const [history, setHistory] = useState([])
  const [latest, setLatest] = useState(null)
  const [alertSnapshot, setAlertSnapshot] = useState(null)
  const [alertsBusy, setAlertsBusy] = useState(false)
  const [alertsRefresh, setAlertsRefresh] = useState(0)
  const [week, setWeek] = useState(null)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  async function load() {
    setError('')
    try {
      const [office] = await api('/locations')
      const [decisions, savedWeek] = await Promise.all([api(`/locations/${office.id}/decisions`), api(`/locations/${office.id}/week-plan`)])
      const current = decisions.find(row => row.evaluated_at === office.last_checked_at)
      const detail = current ? await api(`/decisions/${current.id}`) : null
      setFacility(office); setHistory(decisions); setLatest(detail); setWeek(savedWeek)
    } catch (error) { setError(error.message) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => { setAlertSnapshot(null) }, [facility?.id, facility?.latitude, facility?.longitude])
  useEffect(() => {
    if (!facility) return
    let active = true
    let checking = false
    async function checkAlerts() {
      if (checking) return
      checking = true
      setAlertsBusy(true)
      try {
        const snapshot = await api(`/locations/${facility.id}/weather-alerts`)
        if (active) setAlertSnapshot(previous => snapshot.available ? snapshot : { ...snapshot, alerts: previous?.alerts || [] })
      } catch (error) {
        if (active) setAlertSnapshot(previous => ({ ...previous, available: false, error: error.message }))
      } finally { checking = false; if (active) setAlertsBusy(false) }
    }
    checkAlerts()
    const timer = setInterval(checkAlerts, 60000)
    return () => { active = false; clearInterval(timer) }
  }, [facility?.id, facility?.latitude, facility?.longitude, alertsRefresh])
  async function evaluate() {
    setBusy(true); setError('')
    try {
      const result = await api(`/locations/${facility.id}/evaluate`, { method: 'POST' })
      if (result.state === 'INSUFFICIENT_DATA') setError(`INSUFFICIENT DATA — ${result.message} Last known recommendation was preserved.`)
      else window.location.hash = `decision/${result.decision_id}`
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  async function evaluateWeek() {
    setBusy(true); setError('')
    try {
      const result = await api(`/locations/${facility.id}/evaluate-week`, { method: 'POST' })
      setWeek(result)
      if (result.state === 'INSUFFICIENT_DATA') setError(result.message)
      else await load()
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  return <main className="container"><div className="page-head"><div><p className="eyebrow">OPERATIONS OVERVIEW</p><h1>Your facility</h1><p className="muted">A recommendation starts the conversation. You make the decision.</p></div>
    <button onClick={() => setForm('add')} disabled={busy || loading}>+ Add facility</button></div>
    <ErrorMessage>{error}</ErrorMessage>
    {loading && <p role="status">Loading your facility…</p>}
    {form && <FacilityForm key={form} facility={form === 'edit' ? facility : null} onCancel={() => setForm(null)} onSaved={() => { setForm(null); load() }} />}
    {facility && <section className="card facility-card"><div className="section-head"><div><p className="eyebrow">LOCATION {facility.id}</p><h2>{facility.name}</h2></div>
      <Badge tone={facility.last_checked_at ? facility.status.toLowerCase() : ''}>{facility.last_checked_at ? label(facility.status) : 'EVALUATION NEEDED'}</Badge></div>
      <p className="muted">{facility.latitude}, {facility.longitude} · {facility.timezone}</p>
      <p className="help">The status above is the latest agent recommendation. You decide the operational action and approve any message.</p>
      <div className="facts"><div><span>Office hours</span><strong>{facility.open_time}–{facility.close_time}</strong></div>
        <div><span>Last checked</span><strong>{when(facility.last_checked_at, facility.timezone)}</strong></div>
        <div><span>Qualitative confidence</span><strong>{facility.status_confidence == null ? '—' : facility.status_confidence.toFixed(2)}</strong></div></div>
      {facility.affected_start && <p className="impact">Impact window: {when(facility.affected_start, facility.timezone)} – {when(facility.affected_end, facility.timezone)} · {facility.timezone}</p>}
      <p>{facility.status_reason || 'No live evaluation has been completed for this facility.'}</p>
      {alertSnapshot ? <WeatherAlerts alerts={alertSnapshot.alerts} current available={alertSnapshot.available} fetchedAt={alertSnapshot.fetched_at}
        error={alertSnapshot.error} sourceUrl={alertSnapshot.source_url} timezone={facility.timezone} onRefresh={() => setAlertsRefresh(value => value + 1)} refreshing={alertsBusy} /> : <p role="status">Checking current NWS alerts…</p>}
      {latest && <a href={`#decision/${latest.id}`}>Review recommendation and choose an action →</a>}
      <div className="actions"><button className="primary" disabled={busy} onClick={evaluateWeek}>{busy ? 'Evaluating live signals…' : 'Check next 7 days ↗'}</button>
        <button disabled={busy} onClick={evaluate}>Evaluate today</button>
        <button disabled={busy} onClick={() => setForm('edit')}>Edit location</button>
        {history[0]?.manager_status === 'PENDING' && <Badge tone="pending">MANAGER REVIEW NEEDED</Badge>}</div>
      <p className="help">The hourly engine advises only. Employee email delivery is disabled.</p>
    </section>}
    {week && <WeeklyPlan week={week} timezone={facility?.timezone} />}
    <section className="card"><div className="section-head"><h2>Decision history</h2><button disabled={busy} onClick={load}>Refresh</button></div>
      {!history.length ? <div className="empty"><strong>A clear record starts here.</strong><p>Run an evaluation to save the evidence, recommendation, and message draft.</p></div> :
      <div className="history">{history.map(row => <a className="history-row" href={`#decision/${row.id}`} key={row.id}>
        <div><strong>{label(row.posture)}</strong><span>{when(row.evaluated_at, facility?.timezone)}</span></div>
        <Badge tone={row.manager_status.toLowerCase()}>{row.manager_status}</Badge><span aria-hidden="true">→</span>
      </a>)}</div>}
    </section>
  </main>
}

function WeeklyPlan({ week, timezone }) {
  return <section className="card"><h2>Next seven days · date and shift review</h2>
    <p>{week.message}</p>{week.evaluated_at && <p className="help">Checked {when(week.evaluated_at, timezone)}. Recheck for updated forecasts.</p>}
    <p className="help">Each affected date, shift and impact window has its own recipient list and editable message. Missing coverage is shown explicitly. Future recommendations do not change today's status.</p>
    {week.days.map(day => <article className="week-day" key={day.date}>
      <div className="section-head"><h3>{new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${day.date}T12:00:00Z`))}</h3>
        <Badge tone={day.posture?.toLowerCase()}>{day.state === 'INSUFFICIENT_DATA' ? 'INSUFFICIENT DATA' : label(day.posture)}</Badge></div>
      <p>{day.reason}</p>{day.warnings?.map((warning, index) => <p className="warning-text" key={index}>{warning}</p>)}
      {day.message_groups?.map(group => <a className="history-row" key={group.decision_id} href={`#decision/${group.decision_id}`}>
        <div><strong>{group.shift_name} · {label(group.posture)}</strong><span>{when(group.start, timezone)} – {when(group.end, timezone)}</span></div>
        <span>{group.recipient_count} recipients · {group.manager_status || 'PENDING'} · review draft →</span></a>)}
      {day.state === 'COMPLETED' && !day.message_groups?.length && <p className="help">No affected on-site shift recipients.
        {day.decision_ids?.[0] && <> <a href={`#decision/${day.decision_ids[0]}`}>Review day / choose an action →</a></>}</p>}
    </article>)}
  </section>
}

function EmployeeTable({ rows, onToggle, busy, scope }) {
  return <div className="table-scroll"><table><thead><tr><th>Include</th><th>Employee</th><th>Shift</th><th>Flags</th><th>Selection reason</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.emp_id} className={row.included ? '' : 'filtered'}><td><input type="checkbox" aria-label={`Include ${row.name}`}
      checked={row.included} disabled={busy || (scope === 'affected' && ['not_scheduled', 'already_remote', 'invalid_shift', 'no_disruption'].includes(row.reason))}
      onChange={e => onToggle(row.emp_id, e.target.checked)} /></td><td><strong>{row.name}</strong><small>{row.email}</small></td>
      <td>{row.shift_name || 'No shift'}<small>{row.weekly_hours ? Object.entries(row.weekly_hours).filter(([, value]) => value).map(([day, hours]) => `${day} ${hours.join('–')}`).join(' · ') : 'No valid schedule'}</small></td>
      <td>{row.is_manager ? <Badge>MANAGER</Badge> : null}{row.is_essential ? <Badge tone="pending">ESSENTIAL</Badge> : null}{row.is_teleworking ? <Badge>REMOTE</Badge> : null}</td>
      <td>{label(row.reason)}{row.warning && <small className="warning-text">{row.warning}</small>}</td></tr>)}</tbody></table></div>
}

function DecisionDetails({ id }) {
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState(null)
  const [rows, setRows] = useState([])
  const [selection, setSelection] = useState({ scope: 'affected', include_ids: [], exclude_ids: [] })
  const [essential, setEssential] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  function apply(value) {
    setDetail(value); setRows(value.recipients)
    setForm({ posture: value.posture, reason: value.reason, draft_subject: value.draft_subject, draft_body: value.draft_body })
    setSelection(value.alternatives.recipient_selection || { scope: 'affected', include_ids: [], exclude_ids: [] })
    setEssential(false); setDirty(false)
  }
  useEffect(() => {
    let active = true
    api(`/decisions/${id}`).then(value => { if (active) apply(value) }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [id])
  async function preview(next, posture = form.posture) {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api(`/decisions/${id}/recipients`, { method: 'POST', body: { ...next, posture } })
      setRows(result); setSelection(next); setDirty(true); setEssential(false)
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  function toggle(empId, included) {
    preview({ ...selection,
      include_ids: included ? [...new Set([...selection.include_ids, empId])] : selection.include_ids.filter(id => id !== empId),
      exclude_ids: included ? selection.exclude_ids.filter(id => id !== empId) : [...new Set([...selection.exclude_ids, empId])],
    })
  }
  function edit(key, value) { setForm(current => ({ ...current, [key]: value })); setDirty(true); setNotice('') }
  async function save(status) {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api(`/decisions/${id}/review`, { method: 'POST', body: { ...form, ...selection, status, essential_confirmed: essential } })
      apply(result); setNotice(`${status === 'PENDING' ? 'Draft saved' : label(status)}. No emails were sent.`)
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  function download() {
    const blob = new Blob([`Subject: ${detail.draft_subject}\n\n${detail.draft_body}`], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = `decision-${id}-${detail.manager_status.toLowerCase()}.txt`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  if (!detail) return <main className="container"><a href="#">← Facilities</a><ErrorMessage>{error}</ErrorMessage><p role="status">{error ? 'Decision could not be loaded.' : 'Loading decision…'}</p></main>
  const zone = detail.alternatives.facility_snapshot?.timezone
  const included = rows.filter(row => row.included)
  const needsEssential = included.some(row => row.requires_manager_review)
  return <main className="container"><a className="back" href="#">← Back to facilities</a>
    <div className="page-head"><div><p className="eyebrow">DECISION {detail.id} · {when(detail.evaluated_at, zone)}</p><h1>{label(detail.posture)}</h1><p className="muted">{detail.manager_status === 'PENDING' ? 'Recommendation only — awaiting your decision. ' : `${detail.manager_status} by manager. `}{detail.alternatives.facility_snapshot?.name || `Facility ${detail.location_id}`} · {when(detail.affected_start, zone)} – {when(detail.affected_end, zone)} {zone}</p></div>
      <Badge tone={detail.manager_status.toLowerCase()}>{detail.manager_status}</Badge></div>
    <ErrorMessage>{error}</ErrorMessage>{notice && <div className="notice success" role="status">{notice}</div>}
    {detail.alternatives.message_group?.shift_name && <div className="notice" role="status">This draft applies to {detail.alternatives.message_group.shift_name} on {detail.alternatives.message_group.date}. Affected-staff previews stay within this shift; facility-wide scope explicitly widens the audience.</div>}
    <WeatherAlerts alerts={detail.alternatives.inputs?.weather?.alerts} timezone={zone} />
    <section className="card"><div className="section-head"><h2>Why this recommendation</h2><Badge>{detail.confidence.toFixed(2)} QUALITATIVE CONFIDENCE</Badge></div>
      <p>{detail.reason}</p><p className="help">Qualitative estimate, not a statistically calibrated probability. All posture actions are app-defined demo policy.</p>
      <div className="two-column"><div><h3>Uncertainty</h3><p>{detail.alternatives.uncertainty || 'Requires manager review.'}</p>
        {detail.alternatives.warnings?.map((warning, index) => <p className="warning-text" key={index}>{warning}</p>)}</div>
        <div><h3>What would change this</h3><ul>{detail.alternatives.what_would_change?.map((item, index) => <li key={index}>{item}</li>)}</ul></div></div>
      <h3>Policy thresholds</h3><div className="table-scroll"><table><thead><tr><th>Rule</th><th>Threshold</th><th>Actual / margin</th></tr></thead><tbody>{detail.thresholds.map((row, index) => <tr key={index}>
        <td>{row.rule}<small>{row.policy_version} · {row.kind}</small></td><td>{row.threshold}</td><td>{row.actual}<small>{row.margin}</small></td></tr>)}</tbody></table></div>
      <h3>Source evidence</h3><div className="evidence-grid">{detail.evidence.map((row, index) => <article className="evidence" key={index}>
        <a href={row.url} target="_blank" rel="noreferrer">{row.source} ↗</a><p className="help">Observed / forecast for {when(row.observed_at, zone)}</p>
        {row.value?.modeled && <Badge>MODELED · NOT A SENSOR READING</Badge>}
        <details><summary>View source values</summary><pre>{JSON.stringify(row.value, null, 2)}</pre></details></article>)}</div>
    </section>
    <section className="card"><div className="section-head"><div><h2>Employee selection</h2><p className="muted">{included.length} included · {rows.length - included.length} filtered · current preview shown after refresh</p></div>
      <button disabled={busy} onClick={() => preview(selection)}>Refresh current staff</button></div>
      <label className="scope">Message audience<select value={selection.scope} disabled={busy} onChange={e => preview({ scope: e.target.value, include_ids: [], exclude_ids: [] })}>
        <option value="affected">Affected on-site staff · schedule matched</option><option value="all_staff">Facility-wide · includes remote and unscheduled staff</option></select></label>
      <p className="help">Commute notices exclude remote and unscheduled staff. Choose facility-wide for wider safety messages. Managers are excluded unless selected.</p>
      <EmployeeTable rows={rows} onToggle={toggle} busy={busy} scope={selection.scope} />
    </section>
    <section className="card"><p className="eyebrow">MANAGER REVIEW</p><h2>Prepare the message</h2><p className="muted">Edit the recommendation and text. Approval prepares your message; email delivery is disabled.</p>
      <form onSubmit={e => { e.preventDefault(); save('PENDING') }}><fieldset disabled={busy}>
        <label>Manager posture<select value={form.posture} onChange={e => { const posture = e.target.value; edit('posture', posture); preview({ scope: selection.scope, include_ids: [], exclude_ids: [] }, posture) }}>{POSTURES.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
        <label>Reason<textarea rows="3" minLength="20" maxLength="3000" required value={form.reason} onChange={e => edit('reason', e.target.value)} /></label>
        <label>Subject<input maxLength="240" required value={form.draft_subject} onChange={e => edit('draft_subject', e.target.value)} /></label>
        <label>Message<textarea className="message-input" rows="11" maxLength="20000" required value={form.draft_body} onChange={e => edit('draft_body', e.target.value)} /></label>
        {needsEssential && <label className="check-notice"><input type="checkbox" checked={essential} onChange={e => setEssential(e.target.checked)} />I have confirmed specific instructions for the included essential personnel.</label>}
        <div className="actions"><button className="primary" type="button" disabled={busy || (needsEssential && !essential)} onClick={() => save('APPROVED')}>Approve message</button>
          <button type="submit">Save draft</button><button className="danger" type="button" onClick={() => save('REJECTED')}>Reject</button>
          <button type="button" disabled={busy || dirty} onClick={download}>Download saved message ↓</button></div>
      </fieldset></form><p className="help">{dirty ? 'You have unsaved changes. Save or approve before downloading.' : `Saved as ${detail.manager_status}. No manager or employee email has been sent.`}</p>
    </section>
    <section className="card"><h2>Decision record</h2><p>Reviewed by: {detail.reviewed_by || 'Awaiting manager'} · {detail.reviewed_at ? when(detail.reviewed_at, zone) : 'Pending'}</p>
      <p>Manager alert: {detail.alternatives.material_change ? 'New or changed alert/recommendation/window — review needed in app.' : 'No material change since the previous evaluation. Source alerts remain visible.'} Email delivery disabled.</p>
      <details><summary>Original inputs, recommendation and review history</summary><pre>{JSON.stringify(detail.alternatives, null, 2)}</pre></details>
    </section>
  </main>
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(sessionStorage.getItem('facility_token')))
  const [decisionId, setDecisionId] = useState(decisionFromHash())
  function logout() { sessionStorage.removeItem('facility_token'); sessionStorage.removeItem('facility_manager'); setLoggedIn(false); window.location.hash = '' }
  useEffect(() => {
    const navigate = () => setDecisionId(decisionFromHash())
    window.addEventListener('hashchange', navigate); window.addEventListener('session-expired', logout)
    return () => { window.removeEventListener('hashchange', navigate); window.removeEventListener('session-expired', logout) }
  }, [])
  if (!loggedIn) return <Login onLogin={() => setLoggedIn(true)} />
  return <><header className="app-header"><a className="brand" href="#"><span className="brand-mark">F</span>Facility Operations <span className="brand-ai">AI</span></a>
    <div><span className="manager-name">{sessionStorage.getItem('facility_manager')}</span><button onClick={logout}>Sign out</button></div></header>
    <div className="safety-strip">MANAGER IN THE LOOP <span>Live evidence. Human approval. Email delivery disabled.</span></div>
    {decisionId ? <DecisionDetails key={decisionId} id={decisionId} /> : <Dashboard />}
    <footer>Facility Operations AI · Hackathon MVP · Not an emergency warning system</footer></>
}
