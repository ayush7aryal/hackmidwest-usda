import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import FacilityMap from './FacilityMap.jsx'

const POSTURES = ['NORMAL', 'DELAYED_OPENING', 'EARLY_DISMISSAL', 'TELEWORK', 'CLOSED']
const LABELS = { NORMAL: 'Normal operations', DELAYED_OPENING: 'Delayed opening', EARLY_DISMISSAL: 'Early dismissal', TELEWORK: 'Work remotely', CLOSED: 'Closed', PENDING: 'Needs review', APPROVED: 'Approved', REJECTED: 'Rejected' }
const REASONS = { scheduled_on_site: 'Scheduled on-site', manager_selected: 'Selected by manager', facility_wide: 'Facility-wide notice', not_scheduled: 'Outside affected hours', already_remote: 'Already remote', manager: 'Manager', no_disruption: 'No schedule change', normal_operations: 'Normal operations · no notification needed', unchanged_operation: 'Refresh staff to check notification eligibility', already_notified: 'Already notified for this window', delivery_uncertain: 'Previous email submission unresolved', invalid_shift: 'Schedule needs correction', manager_excluded: 'Excluded by manager' }
const label = value => LABELS[value] || value?.replaceAll('_', ' ') || '—'
const decisionFromHash = () => window.location.hash.match(/^#decision\/(\d+)$/)?.[1] || null
const audienceDefault = () => ({ scope: 'affected', include_ids: [], exclude_ids: [] })
const impactExpired = detail => new Date(detail.affected_end).getTime() <= Date.now()

function recipientState(rows) {
  return JSON.stringify(rows.map(row => [row.emp_id, row.name, row.email, row.shift_id, row.weekly_hours,
    Boolean(row.included), Boolean(row.is_essential), Boolean(row.is_teleworking), Boolean(row.is_manager),
    Boolean(row.requires_manager_review), row.reason]))
}

function when(value, timezone, clockOnly = false) {
  if (!value) return 'Not checked yet'
  const instant = new Date(value)
  if (!Number.isFinite(instant.getTime())) return 'Time unavailable'
  const options = clockOnly ? { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' } : { dateStyle: 'medium', timeStyle: 'short' }
  return new Intl.DateTimeFormat('en-US', { ...options, ...(timezone ? { timeZone: timezone } : {}) }).format(instant)
}

function calendarDate(value, short = false) {
  return new Intl.DateTimeFormat('en-US', { weekday: short ? 'short' : 'long', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))
}

function Badge({ children, tone = '' }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

function ErrorMessage({ children }) {
  return children ? <div className="notice error" role="alert">{children}</div> : null
}

function ReasonText({ text = '' }) {
  const [expanded, setExpanded] = useState(false)
  const long = text.length > 260
  return <div className="reason-text"><p>{long && !expanded ? `${text.slice(0, 260).replace(/\s+\S*$/, '')}…` : text}</p>
    {long && <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? 'Show less' : 'Read full explanation'}</button>}</div>
}

function SourceNotes({ warnings = [] }) {
  if (!warnings.length) return null
  const limited = warnings.some(item => /unavailable|incomplete|coverage/i.test(item))
  return <details className="source-notes"><summary>{limited ? 'Limited forecast coverage' : 'Conditions need manager review'} <span>· {warnings.length} source note{warnings.length === 1 ? '' : 's'}</span></summary>
    <ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>
}

function WeatherAlerts({ alerts = [], timezone, current = false, available = true, fetchedAt, sourceUrl, error, onRefresh, refreshing }) {
  if (!current && !alerts.length) return null
  return <aside className={`weather-alerts ${!alerts.length && available ? 'quiet-alerts' : ''}`} aria-label="NWS weather alerts">
    <div className="section-head"><div><h2>{alerts.length ? `${alerts.length} weather alert${alerts.length === 1 ? '' : 's'}` : available ? 'No active NWS alerts' : 'Weather alerts unavailable'}</h2>
      {current && <p className="help">{fetchedAt ? `Updated ${when(fetchedAt, timezone)}` : 'Checking NWS'} · refreshes every minute</p>}</div>
      <div className="inline-actions">{sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">NWS ↗</a>}{onRefresh && <button className="small-button" disabled={refreshing} onClick={onRefresh}>{refreshing ? 'Checking…' : 'Refresh'}</button>}</div></div>
    {!available && <p className="warning-text" role="alert">{error || 'Current alerts could not be checked.'} {alerts.length ? 'Saved alerts below may be outdated.' : 'Check the NWS source.'}</p>}
    {available && !alerts.length && <p className="help">No active alerts does not guarantee hazard-free conditions.</p>}
    {alerts.map((alert, index) => {
      const urgent = ['Severe', 'Extreme'].includes(alert.severity) && alert.status === 'Actual' && alert.messageType !== 'Cancel'
      const status = alert.messageType === 'Cancel' ? 'Canceled' : alert.status !== 'Actual' ? alert.status || 'Status unknown' : null
      return <article className="alert-item" key={alert.url || index}>
        <div className="section-head"><a href={alert.url} target="_blank" rel="noreferrer"><strong>{alert.event || 'NWS alert'} ↗</strong></a><div className="inline-actions"><Badge tone={urgent ? 'closed' : 'pending'}>{alert.severity || 'Unknown severity'}</Badge>{status && <Badge>{status}</Badge>}</div></div>
        <p className="help">{when(alert.onset || alert.effective, timezone)} – {when(alert.ends || alert.expires, timezone)}</p>
        {urgent && <p className="alert-description" role="alert"><strong>NWS instructions: </strong>{alert.instruction || 'Read the full alert for instructions.'}</p>}
        <details><summary>{urgent ? 'More alert details' : 'Read instructions and details'}</summary>
          {!urgent && <p className="alert-description"><strong>NWS instructions: </strong>{alert.instruction || 'No separate instructions provided. Check the NWS source.'}</p>}
          {alert.headline && <p><strong>{alert.headline}</strong></p>}
          <p className="alert-description">{alert.description || 'No description provided.'}</p>
          <p className="help">{alert.urgency || 'Unknown urgency'} · {alert.certainty || 'Unknown certainty'} · {alert.status || 'Unknown status'} · {alert.messageType || 'Unknown message type'}</p>
          {alert.areaDesc && <p className="help">{alert.areaDesc}</p>}{alert.response && <p className="help">NWS response: {alert.response}</p>}
        </details>
      </article>
    })}
  </aside>
}

function Login({ onLogin }) {
  const [employee, setEmployee] = useState('1001')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await api('/login', { method: 'POST', body: { emp_id: Number(employee), password } })
      sessionStorage.setItem('facility_token', result.access_token)
      sessionStorage.setItem('facility_manager', result.manager.name)
      onLogin()
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  return <main className="login-layout"><section className="login-intro"><p className="eyebrow">FACILITY OPERATIONS</p>
    <h1>A clearer plan.<br />A prepared team.</h1><p>See upcoming disruptions, review affected shifts, and prepare your team's message.</p>
    <div className="login-flow"><span>01 Check the week</span><span>02 Review your team</span><span>03 Prepare a message</span></div>
  </section><section className="card login-card"><p className="eyebrow">MANAGER ACCESS</p><h2>Welcome back</h2>
    <form onSubmit={submit}><label>Manager account<select value={employee} onChange={event => setEmployee(event.target.value)}>
      <option value="1001">Alex Morgan · 1001</option><option value="2001">Jordan Lee · 2001</option>
      <option value="51001">Maya Patel · 51001 · Panama City Beach</option><option value="52001">Daniel Brooks · 52001 · Jacksonville</option>
      <option value="53001">Sofia Ramirez · 53001 · Charleston</option><option value="54001">Ethan Chen · 54001 · Savannah</option>
      <option value="55001">Olivia Bennett · 55001 · Tampa</option></select></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} placeholder="Manager password" /></label>
      <details className="login-help"><summary>Local demo sign-in help</summary><p className="help">Use the configured manager password. Leave blank only if no password is configured.</p></details>
      <ErrorMessage>{error}</ErrorMessage><button className="primary full" disabled={busy}>{busy ? 'Signing in…' : 'Open dashboard →'}</button>
    </form><p className="help login-foot">Synthetic employees · Manager-reviewed messages</p></section></main>
}

function FacilityForm({ facility, onSaved, onCancel }) {
  const [values, setValues] = useState(facility || { name: '', latitude: '', longitude: '', timezone: 'America/Chicago', open_time: '08:00', close_time: '18:00', id: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fields = [['name', 'Facility name', 'text'], ['id', 'Location ID', 'number'], ['latitude', 'Latitude', 'number'], ['longitude', 'Longitude', 'number'], ['timezone', 'Timezone', 'text'], ['open_time', 'Opens at', 'time'], ['close_time', 'Closes at', 'time']]
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
  return <section className="card"><div className="section-head"><div><p className="eyebrow">LOCATION SETTINGS</p><h1>{facility ? 'Edit facility' : 'Add facility'}</h1></div><button onClick={onCancel} disabled={busy}>Cancel</button></div>
    <p className="muted">{facility ? 'Save your changes, then check the forecast again.' : 'Your existing demo employee sheet moves to this facility.'}</p>
    <form onSubmit={submit}><label className="facility-name">Facility name<input required maxLength="200" value={values.name} placeholder="e.g. Panama City Office" onChange={event => setValues({ ...values, name: event.target.value })} /></label>
      <FacilityMap latitude={values.latitude} longitude={values.longitude} onPick={point => setValues(previous => ({ ...previous, ...point }))} />
      <h2>Facility details</h2><div className="form-grid">{fields.filter(([key]) => key !== 'name').map(([key, title, type]) => <label key={key}>{title}<input type={type} value={values[key]} required={key !== 'id'}
      min={key === 'id' ? 1 : key === 'latitude' ? -90 : key === 'longitude' ? -180 : undefined} max={key === 'latitude' ? 90 : key === 'longitude' ? 180 : undefined}
      step={['latitude', 'longitude'].includes(key) ? 'any' : undefined} placeholder={key === 'id' ? 'Automatic if blank' : key === 'timezone' ? 'America/Chicago' : undefined}
      onChange={event => setValues({ ...values, [key]: event.target.value })} /></label>)}</div>
      <ErrorMessage>{error}</ErrorMessage><button className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save facility'}</button></form>
  </section>
}

function EvaluationLoader({ weekly }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setSeconds(value => value + 1), 1000)
    return () => clearInterval(timer)
  }, [])
  return <div className="evaluation-progress" role="status" aria-live="polite">
    <span className="spinner" aria-hidden="true" /><div><strong>{weekly ? 'Checking the next 7 days' : 'Evaluating today'}</strong>
      <p className="help">Fetching live sources and preparing AI advice for affected shifts.</p>
      <span className="help">{seconds < 30 ? 'Please keep this page open.' : 'The source or model service is taking longer. Your saved plan is still available.'}</span></div>
    <span className="elapsed" aria-hidden="true">{seconds}s</span>
  </div>
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
  const [busy, setBusy] = useState('')
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
  useEffect(() => {
    if (busy || form) return
    const timer = setInterval(() => load(), 60000)
    return () => clearInterval(timer)
  }, [busy, form])
  useEffect(() => { setAlertSnapshot(null) }, [facility?.id, facility?.latitude, facility?.longitude])
  useEffect(() => {
    if (!facility) return
    let active = true
    let checking = false
    async function checkAlerts() {
      if (checking) return
      checking = true; setAlertsBusy(true)
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
  async function evaluate(weekly) {
    setBusy(weekly ? 'week' : 'today'); setError('')
    try {
      const result = await api(`/locations/${facility.id}/${weekly ? 'evaluate-week' : 'evaluate'}`, { method: 'POST' })
      if (result.state === 'INSUFFICIENT_DATA') {
        setError(`${result.message}${week && weekly ? ' Showing the previous weekly check.' : ' Previous recommendations were preserved.'}`)
        if (weekly && !week && result.days?.length) setWeek(result)
      } else if (weekly) await load()
      else window.location.hash = `decision/${result.decision_id}`
    } catch (error) { setError(error.message) } finally { setBusy('') }
  }
  if (form) return <main className="container narrow"><FacilityForm key={form} facility={form === 'edit' ? facility : null} onCancel={() => setForm(null)} onSaved={() => { setForm(null); load() }} /></main>
  return <main className="container"><div className="page-head"><div><p className="eyebrow">YOUR FACILITY</p><h1>{facility?.name || 'Operations dashboard'}</h1>
    <p className="muted">Plan the week. Prepare your team.</p></div><div className="inline-actions"><button disabled={Boolean(busy) || loading || !facility} onClick={() => setForm('edit')}>Edit location</button>
      <button className="text-button" disabled={Boolean(busy) || loading} onClick={() => setForm('add')}>+ Add facility</button></div></div>
    <ErrorMessage>{error}</ErrorMessage>{loading && <p role="status">Loading your facility…</p>}
    {facility && <><section className="card overview-card"><div><p className="eyebrow">LATEST DAILY CHECK</p>
      <Badge tone={facility.last_checked_at ? facility.status.toLowerCase() : 'pending'}>{facility.last_checked_at ? label(facility.status) : 'Check needed'}</Badge>
      <p className="help">{facility.open_time}–{facility.close_time} · {facility.timezone} · {when(facility.last_checked_at, facility.timezone)}</p></div>
      <div className="inline-actions"><button className="primary" disabled={Boolean(busy)} onClick={() => evaluate(true)}>{busy === 'week' ? 'Checking the week…' : 'Check next 7 days ↗'}</button>
        <button disabled={Boolean(busy)} onClick={() => evaluate(false)}>{busy === 'today' ? 'Checking today…' : 'Evaluate today'}</button></div>
      <details className="daily-summary"><summary>Daily recommendation & location details</summary><ReasonText text={facility.status_reason || 'Check the forecast to get a recommendation.'} />
        <p className="help">Location {facility.id} · {facility.latitude}, {facility.longitude}</p>
        {facility.affected_start && <p className="help">{when(facility.affected_start, facility.timezone)} – {when(facility.affected_end, facility.timezone)}</p>}
        {latest && <a href={`#decision/${latest.id}`}>Review daily recommendation →</a>}</details>
      {busy && <EvaluationLoader weekly={busy === 'week'} />}
    </section>
    {['FAILED', 'UNKNOWN', 'PARTIAL'].includes(latest?.alternatives.manager_alert?.status) && <p className="essential-note" role="status">The manager review email needs attention. <a href={`#decision/${latest.id}`}>Review the saved decision →</a></p>}
    {alertSnapshot ? <WeatherAlerts alerts={alertSnapshot.alerts} current available={alertSnapshot.available} fetchedAt={alertSnapshot.fetched_at} error={alertSnapshot.error}
      sourceUrl={alertSnapshot.source_url} timezone={facility.timezone} onRefresh={() => setAlertsRefresh(value => value + 1)} refreshing={alertsBusy} /> : <p className="help" role="status">Checking current NWS alerts…</p>}
    </>}
    {week ? <WeeklyPlan key={week.evaluated_at || week.message} week={week} timezone={facility?.timezone} /> : !loading && <section className="card empty-plan"><span className="empty-symbol" aria-hidden="true">↗</span><h2>Start with the week ahead</h2>
      <p>Check the forecast to see affected dates, shifts and message drafts.</p><div className="mini-flow"><span>1 · Check forecast</span><span>2 · Review shifts</span><span>3 · Prepare message</span></div></section>}
    <details className="card history-panel"><summary>Past checks <span>{history.length} recent records</span></summary>
      <div className="section-head"><p className="help">Saved recommendations and messages</p><button className="small-button" disabled={Boolean(busy)} onClick={load}>Refresh</button></div>
      {!history.length ? <p className="muted">Your checks will appear here.</p> : history.map(row => <a className="history-row" href={`#decision/${row.id}`} key={row.id}><div><strong>{label(row.posture)}</strong><span>{when(row.evaluated_at, facility?.timezone)}</span></div><Badge tone={row.manager_status.toLowerCase()}>{label(row.manager_status)}</Badge><span aria-hidden="true">→</span></a>)}
    </details>
  </main>
}

function WeeklyPlan({ week, timezone }) {
  const days = week.days || []
  const preferred = days.find(day => day.message_groups?.some(group => group.manager_status === 'PENDING')) || days.find(day => day.posture && day.posture !== 'NORMAL') || days[0]
  const [selected, setSelected] = useState(preferred?.date)
  const day = days.find(item => item.date === selected) || days[0]
  const groups = days.flatMap(item => item.message_groups || [])
  const pending = groups.filter(item => item.manager_status === 'PENDING').length
  const unavailable = days.filter(item => item.state === 'INSUFFICIENT_DATA').length
  const mixedActions = new Set(day?.message_groups?.map(group => group.posture)).size > 1
  return <section className="card week-plan"><div className="section-head"><div><p className="eyebrow">WEEK AHEAD</p><h2>Your week at a glance</h2></div>
    <div className="inline-actions"><Badge tone={pending ? 'pending' : ''}>{pending ? `${pending} draft${pending === 1 ? '' : 's'} to review` : 'No pending shift drafts'}</Badge>{unavailable > 0 && <Badge tone="pending">{unavailable} day{unavailable === 1 ? '' : 's'} unavailable</Badge>}</div></div>
    {week.evaluated_at && <p className="help">Checked {when(week.evaluated_at, timezone)} · Select a day below</p>}
    <div className="week-strip" role="group" aria-label="Choose a forecast date">{days.map(item => <button key={item.date} className={`day-button ${item.date === day?.date ? 'selected' : ''}`} aria-pressed={item.date === day?.date} aria-label={calendarDate(item.date)} onClick={() => setSelected(item.date)}>
      <span className="day-name">{calendarDate(item.date, true).split(',')[0]}</span><strong>{new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(`${item.date}T12:00:00Z`))}</strong>
      <span className={`day-signal ${item.state === 'INSUFFICIENT_DATA' || item.warnings?.length ? 'attention' : item.message_groups?.length ? 'affected' : ''}`}>
        {item.state === 'INSUFFICIENT_DATA' ? 'Unavailable' : item.message_groups?.length ? `${item.message_groups.length} shift draft${item.message_groups.length === 1 ? '' : 's'}` : item.warnings?.length ? 'Review notes' : item.posture !== 'NORMAL' ? 'Review day' : 'No change'}</span>
    </button>)}</div>
    {day ? <div className="selected-day" role="region" aria-labelledby="selected-day-title"><div className="section-head"><h3 id="selected-day-title">{calendarDate(day.date)}</h3><Badge tone={day.state === 'INSUFFICIENT_DATA' ? 'pending' : day.posture?.toLowerCase()}>{day.state === 'INSUFFICIENT_DATA' ? 'Forecast unavailable' : mixedActions ? 'Multiple shift actions' : label(day.posture)}</Badge></div>
      <ReasonText key={day.date} text={day.reason} /><SourceNotes warnings={day.warnings} />
      {day.message_groups?.map(group => <a className="shift-draft" key={group.decision_id} href={`#decision/${group.decision_id}`}><div><strong>{group.shift_name}</strong><span>{label(group.posture)} · {when(group.start, timezone, true)} – {when(group.end, timezone, true)} · {group.recipient_count} recipient{group.recipient_count === 1 ? '' : 's'}</span></div>
        <Badge tone={group.manager_status?.toLowerCase()}>{label(group.manager_status || 'PENDING')}</Badge><span className="draft-cta">{group.manager_status === 'PENDING' ? 'Review draft' : 'Open message'} →</span></a>)}
      {day.state === 'COMPLETED' && !day.message_groups?.length && <div className="day-empty"><span>No affected on-site shifts.</span>{day.decision_ids?.[0] && <a href={`#decision/${day.decision_ids[0]}`}>View day assessment →</a>}</div>}
      {day.state === 'INSUFFICIENT_DATA' && <p className="help">No all-clear or employee draft is available for this date.</p>}
    </div> : <p className="muted">{week.message}</p>}
  </section>
}

function shiftHours(row, affectedStart, timezone) {
  if (!affectedStart || !Number.isFinite(new Date(affectedStart).getTime())) return 'Schedule unavailable'
  const key = new Intl.DateTimeFormat('en-US', { weekday: 'short', ...(timezone ? { timeZone: timezone } : {}) }).format(new Date(affectedStart)).toLowerCase()
  const hours = row.weekly_hours?.[key]
  return hours ? `${key} ${hours.join('–')}` : 'Not scheduled this day'
}

function EmployeeTable({ rows, onToggle, busy, affectedStart, timezone }) {
  return <div className="table-scroll"><table><thead><tr><th><span className="sr-only">Include</span></th><th>Employee</th><th>Affected shift</th><th>Selection</th></tr></thead><tbody>
    {rows.map(row => <tr key={row.emp_id} className={row.included ? '' : 'filtered'}><td><input type="checkbox" aria-label={`Include ${row.name}`} checked={row.included}
      disabled={busy} onChange={event => onToggle(row.emp_id, event.target.checked)} /></td>
      <td><strong>{row.name}</strong><small>{row.email}</small><div className="employee-flags">{row.is_essential && <Badge tone="pending">Essential</Badge>}{row.is_teleworking && <Badge>Remote</Badge>}{row.is_manager && <Badge>Manager</Badge>}</div></td>
      <td>{row.shift_name || 'No shift'}<small>{shiftHours(row, affectedStart, timezone)}</small></td><td>{REASONS[row.reason] || label(row.reason)}{row.previous_delivery && <small>{REASONS[row.previous_delivery]}</small>}</td></tr>)}
  </tbody></table></div>
}

function RecipientStep({ rows, selection, busy, onPreview, onToggle, detail, timezone }) {
  const [view, setView] = useState('all')
  const included = rows.filter(row => row.included)
  const visible = view === 'all' ? rows : rows.filter(row => view === 'included' ? row.included : !row.included)
  const emptyMessage = rows.some(row => ['normal_operations', 'no_disruption'].includes(row.reason))
    ? 'Normal operations: no automatic employee notice is needed. Select employees below or choose everyone to send a manager notice.'
    : 'No eligible employees selected. Open Excluded to see why, or select affected staff to clear manual exclusions.'
  return <><div className="section-head"><div><p className="eyebrow">STEP 2 OF 3</p><h2>Who needs this message?</h2></div><div className="inline-actions">
    {selection.scope === 'affected' && <button disabled={busy} className="small-button" onClick={() => { setView('all'); onPreview(audienceDefault()) }}>Select affected staff</button>}
    <button disabled={busy} className="small-button" onClick={() => onPreview(selection)}>Refresh staff</button></div></div>
    <label>Audience<select disabled={busy} value={selection.scope} onChange={event => { setView('all'); onPreview({ scope: event.target.value, include_ids: [], exclude_ids: [] }) }}>
      <option value="affected">Affected on-site employees</option><option value="all_staff">Everyone at this facility</option></select></label>
    <p className="help">{selection.scope === 'affected' ? 'Suggested from affected shifts. Check or uncheck anyone below.' : 'All facility staff selected, including remote staff and managers. Uncheck anyone to exclude them.'}</p>
    <div className="segmented" role="group" aria-label="Recipient list"><button aria-pressed={view === 'all'} onClick={() => setView('all')}>All ({rows.length})</button><button aria-pressed={view === 'included'} onClick={() => setView('included')}>Selected ({included.length})</button><button aria-pressed={view === 'excluded'} onClick={() => setView('excluded')}>Excluded ({rows.length - included.length})</button></div>
    {included.some(row => row.requires_manager_review) && <p className="essential-note">Essential staff are selected. Confirm their instructions in the next step.</p>}
    {busy && <p className="help" role="status">Updating selection…</p>}
    {visible.length ? <EmployeeTable rows={visible} busy={busy} onToggle={onToggle} affectedStart={detail.affected_start} timezone={timezone} /> : <div className="small-empty">{view === 'included' ? emptyMessage : rows.length ? 'No excluded employees.' : 'No staff assigned to this facility.'}</div>}
  </>
}

function DecisionEvidence({ detail, timezone }) {
  return <details className="disclosure evidence-panel"><summary>Evidence & policy <span className="summary-meta">{detail.evidence.length} sources · Confidence {Math.round(detail.confidence * 100)}%</span></summary>
    <p className="help">Confidence is a qualitative estimate, not a probability.</p>
    <details className="disclosure"><summary>Sources ({detail.evidence.length})</summary>
      <div className="evidence-grid">{detail.evidence.map((row, index) => <article className="evidence" key={index}>
        <a href={row.url} target="_blank" rel="noreferrer">{row.source} ↗</a><p className="help">{when(row.observed_at, timezone)}</p>
        {row.value?.modeled && <Badge>Modeled AQI</Badge>}<details><summary>Technical source data</summary><pre>{JSON.stringify(row.value, null, 2)}</pre></details>
      </article>)}</div>
    </details>
    <details className="disclosure"><summary>Policy thresholds ({detail.thresholds.length})</summary>
      <p className="help">Fixed demo rules with reference guidance. The agent explains; you decide.</p>
      <div className="policy-list">{detail.thresholds.map((row, index) => <article key={index}>
        <strong>{row.rule}</strong><p>{row.actual}<span className="muted"> · {row.margin}</span></p>
        <details><summary>Trigger & reference</summary><p>{row.threshold}</p><p className="help">{row.policy_version}{row.provenance && <> · <a href={row.provenance} target="_blank" rel="noreferrer">Reference guidance ↗</a></>}</p></details>
      </article>)}</div>
    </details>
    <details className="disclosure"><summary>Uncertainty & what could change</summary>
      <p>{detail.alternatives.uncertainty || 'Requires manager review.'}</p>
      <ul>{detail.alternatives.what_would_change?.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </details>
  </details>
}

function RiskStep({ detail, form, busy, onEdit, onPosture, timezone }) {
  const alert = detail.alternatives.manager_alert
  return <><p className="eyebrow">STEP 1 OF 3</p><h2>Review the risk</h2><ReasonText text={form.reason} /><SourceNotes warnings={detail.alternatives.warnings} />
    <WeatherAlerts alerts={detail.alternatives.inputs?.weather?.alerts} timezone={timezone} />
    <div className="operational-action"><label>Your operational action<select disabled={busy} value={form.posture} onChange={event => onPosture(event.target.value)}>{POSTURES.map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label><p className="help">Choose the action for this affected window. Employee selection follows your choice.</p></div>
    <details className="disclosure"><summary>Edit the rationale</summary><label>Reason<textarea disabled={busy} rows="3" minLength="20" maxLength="3000" value={form.reason} onChange={event => onEdit('reason', event.target.value)} /></label></details>
    {alert?.template && <details className="disclosure manager-review-email"><summary>Manager review email · {alert.status === 'ACCEPTED' ? 'Submitted' : label(alert.status)}</summary>
      <p><strong>To:</strong> {alert.template.to.join(', ')}</p><p><strong>Subject:</strong> {alert.template.subject}</p>
      <p className="alert-description">{alert.template.body}</p></details>}
    {['FAILED', 'UNKNOWN', 'PARTIAL'].includes(alert?.status) && <p className="essential-note" role="status">Manager email {label(alert.status).toLowerCase()}. Review this decision here; employee messages have not been sent automatically.</p>}
    <DecisionEvidence detail={detail} timezone={timezone} />
  </>
}

function DeliveryStatus({ delivery, busy, onCheck }) {
  if (!delivery?.status) return null
  const activity = delivery.activity
  const receipts = new Map((activity?.receipts || []).map(receipt => [receipt.to_email, receipt]))
  const delivered = (delivery.targets || []).filter(target => receipts.get(target)?.status === 'delivered').length
  const failed = (delivery.targets || []).filter(target => receipts.get(target)?.status === 'not_delivered').length
  return <div className="help" role="status">
    <p>Employee email: {failed ? `${failed} not delivered; ${delivered} delivered to recipient mail servers.` : delivered ? `${delivered} of ${delivery.targets.length} delivered to recipient mail servers.` : delivery.status === 'ACCEPTED' ? 'Accepted by provider; inbox delivery unconfirmed.' : label(delivery.status)}</p>
    {!!delivery.targets?.length && <details className="disclosure"><summary>Delivery destinations · {delivery.targets.length}{delivery.demo_redirect ? ' · demo redirect' : ''}</summary>
      {delivery.targets.map(target => <p key={target}>{target} · {receipts.get(target)?.status === 'delivered' ? 'Delivered to mail server' : receipts.get(target)?.status === 'not_delivered' ? 'Not delivered' : 'Awaiting delivery confirmation'}</p>)}</details>}
    {activity && <p>{activity.message}</p>}
    {['UNKNOWN', 'PARTIAL', 'IN_PROGRESS'].includes(delivery.status) && <p>Check provider activity before any further action.</p>}
    {delivery.provider === 'sendgrid' && ['ACCEPTED', 'PARTIAL'].includes(delivery.status) && <button type="button" className="text-button" disabled={busy} onClick={onCheck}>Check delivery status</button>}
  </div>
}

function MessageStep({ detail, form, rows, busy, dirty, ready, essential, email, onEssential, onEdit, onSave, onDownload, onRefresh, refreshing, onCheckDelivery }) {
  const selected = rows.filter(row => row.included)
  const needsEssential = selected.some(row => row.requires_manager_review)
  const submitted = Boolean(detail.sent_at || ['PARTIAL', 'ACCEPTED'].includes(detail.delivery?.status))
  const pending = ['IN_PROGRESS', 'UNKNOWN'].includes(detail.delivery?.status)
  return <><p className="eyebrow">STEP 3 OF 3</p><div className="section-head"><h2>Prepare your message</h2><Badge>{selected.length} recipient{selected.length === 1 ? '' : 's'}</Badge></div>
    <p className="help">Review the message, then approve and send it to the selected affected employees.</p>
    {email?.demo_redirect && <p className="essential-note">Demo redirect is enabled: all selected employee messages go only to {email.redirect_address || 'the configured test inbox'}.</p>}
    {email?.sender && <p className="help">From: {email.sender}</p>}
    {impactExpired(detail) && <div className="essential-note" role="status">This assessment's impact window has ended. Refresh for current conditions, or approve a clearly labeled historical demo email.
      <div><button type="button" className="small-button" disabled={busy} onClick={onRefresh}>{refreshing ? 'Refreshing assessment…' : 'Refresh assessment'}</button></div></div>}
    {refreshing && <EvaluationLoader weekly={false} />}
    {form.posture !== detail.posture && <p className="essential-note">Your action changed. Update the message wording to match.</p>}
    {!selected.length && <p className="essential-note">No recipients selected. Choose employees in Recipients.</p>}
    <form onSubmit={event => { event.preventDefault(); onSave(event.nativeEvent.submitter?.value || 'PENDING') }}><fieldset disabled={busy || !ready || pending}>
      <label>Subject<input maxLength="240" required value={form.draft_subject} onChange={event => onEdit('draft_subject', event.target.value)} /></label>
      <label>Message<textarea className="message-input" rows="9" maxLength="20000" required value={form.draft_body} onChange={event => onEdit('draft_body', event.target.value)} /></label>
      {needsEssential && <label className="check-notice"><input type="checkbox" checked={essential} onChange={event => onEssential(event.target.checked)} />I have confirmed instructions for the selected essential staff.</label>}
      <div className="message-actions"><button className="primary" type="submit" value="APPROVED" disabled={!email?.ready || !selected.length || (needsEssential && !essential)}>{busy && !refreshing ? 'Approving and sending…' : 'Approve and send email'}</button><button type="submit" value="PENDING" disabled={submitted}>Save draft</button>
        <button className="text-button danger" type="submit" value="REJECTED" disabled={submitted}>Reject</button></div>
    </fieldset></form>
    <div className="message-actions secondary-actions"><button className="text-button" disabled={busy || dirty} onClick={onDownload}>Download saved message ↓</button></div>
    {submitted && <p className="help">Previously submitted. You can edit and send again after confirmation.</p>}
    {email && !email.ready && <p className="help">Email sending is unavailable. You can save or download the message.</p>}
    <DeliveryStatus delivery={detail.delivery} busy={busy} onCheck={onCheckDelivery} />
    <p className="help" role="status">{dirty ? 'Unsaved changes · save before downloading.' : `Saved · ${label(detail.manager_status)}`}</p>
  </>
}

function ReviewHistory({ detail, timezone }) {
  const original = detail.alternatives.original_recommendation
  const deliveries = [...(detail.alternatives.delivery_history || []), ...(detail.delivery ? [{ delivery: detail.delivery }] : [])]
  return <details className="card history-panel"><summary>Decision record <span>#{detail.id}</span></summary>
    <p className="help">Checked {when(detail.evaluated_at, timezone)}{detail.reviewed_at && ` · Reviewed ${when(detail.reviewed_at, timezone)} by manager ${detail.reviewed_by}`}</p>
    {original && <details className="disclosure"><summary>Original recommendation · {label(original.posture)}</summary><p>{original.reason}</p></details>}
    {deliveries.length > 0 && <details className="disclosure"><summary>Email submissions ({deliveries.length})</summary>
      {deliveries.map(({ delivery }, index) => <p className="help" key={index}>{when(delivery.requested_at, timezone)} · {label(delivery.status)} · {delivery.accepted_employee_ids?.length || 0} employees accepted by provider</p>)}
    </details>}
    {detail.alternatives.reviews?.length ? detail.alternatives.reviews.map((review, index) => <details className="disclosure" key={index}><summary>{label(review.status)} · {when(review.reviewed_at, timezone)}</summary><p>{review.reason}</p><strong>{review.draft_subject}</strong><p className="alert-description">{review.draft_body}</p></details>) : <p className="help">No manager review yet.</p>}
  </details>
}

function DecisionDetails({ id, onDirtyChange, onNavigate }) {
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState(null)
  const [rows, setRows] = useState([])
  const [selection, setSelection] = useState(audienceDefault)
  const [essential, setEssential] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [ready, setReady] = useState(true)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [email, setEmail] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const stepPanel = useRef(null)
  function apply(value) {
    setDetail(value); setRows(value.recipients)
    setForm({ posture: value.posture, reason: value.reason, draft_subject: value.draft_subject, draft_body: value.draft_body })
    setSelection(value.alternatives.recipient_selection || audienceDefault())
    setEssential(Boolean(value.alternatives.reviews?.at(-1)?.essential_confirmed)); setDirty(false); setReady(true)
  }
  useEffect(() => {
    let active = true
    api(`/decisions/${id}`).then(value => { if (active) { apply(value); setStep(value.manager_status === 'PENDING' ? 0 : 2) } }).catch(error => { if (active) setError(error.message) })
    api('/email-status').then(value => { if (active) setEmail(value) }).catch(() => { if (active) setEmail({ ready: false }) })
    return () => { active = false }
  }, [id])
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false) }, [dirty, onDirtyChange])
  useEffect(() => { stepPanel.current?.focus() }, [step, detail?.id])
  async function preview(next, posture = form.posture) {
    setBusy(true); setReady(false); setError(''); setNotice('')
    try {
      const result = await api(`/decisions/${id}/recipients`, { method: 'POST', body: { ...next, posture } })
      if (recipientState(result) !== recipientState(rows) || JSON.stringify(next) !== JSON.stringify(selection)) setDirty(true)
      if (recipientState(result) !== recipientState(rows)) setEssential(false)
      setRows(result); setSelection(next); setReady(true)
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  function toggle(empId, included) {
    preview({ ...selection, include_ids: included ? [...new Set([...selection.include_ids, empId])] : selection.include_ids.filter(value => value !== empId),
      exclude_ids: included ? selection.exclude_ids.filter(value => value !== empId) : [...new Set([...selection.exclude_ids, empId])] })
  }
  function changePosture(posture) {
    const action = posture.replaceAll('_', ' ')
    setForm(current => ({ ...current, posture,
      draft_subject: current.draft_subject.replace(current.posture.replaceAll('_', ' '), action),
      draft_body: current.draft_body.replace(/^Recommended action:.*$/m, `Recommended action: ${action}`) }))
    setDirty(true); setNotice(''); setEssential(false)
    preview(audienceDefault(), posture)
  }
  function edit(key, value) { setForm(current => ({ ...current, [key]: value })); setDirty(true); setNotice('') }
  function changeStep(next) { setStep(next); if (next > 0) preview(selection) }
  async function refreshAssessment() {
    if (busy || (dirty && !window.confirm('Discard unsaved edits and refresh the assessment?'))) return
    setBusy(true); setRefreshing(true); setError(''); setNotice('')
    try {
      const result = await api(`/locations/${detail.location_id}/evaluate`, { method: 'POST' })
      if (result.state !== 'COMPLETED') throw new Error(result.message)
      setDirty(false); onDirtyChange(false)
      window.location.hash = `decision/${result.decision_id}`
    } catch (error) { setError(error.message) } finally { setBusy(false); setRefreshing(false) }
  }
  async function save(status) {
    if (form.reason.trim().length < 20) { setError('Add a rationale of at least 20 characters in the Risk step.'); setStep(0); return }
    if (!ready || busy) return
    const repeat = Boolean(detail.sent_at || ['PARTIAL', 'ACCEPTED'].includes(detail.delivery?.status)
      || detail.alternatives.delivery_history?.length || rows.some(row => row.included && row.previous_delivery))
    const expired = impactExpired(detail)
    const confirmation = expired
      ? `This impact window has ended. Send a clearly labeled HISTORICAL DEMO email with the original dates?${repeat ? ' Previously notified employees will receive it again.' : ''}`
      : 'Some selected employees have already received this notice. Approve and send again?'
    if (status === 'APPROVED' && (repeat || expired) && !window.confirm(confirmation)) return
    let reviewed = false
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api(`/decisions/${id}/review`, { method: 'POST', body: { ...form, ...selection, status, essential_confirmed: essential,
        resend_confirmed: status === 'APPROVED' && repeat, historical_demo_confirmed: status === 'APPROVED' && expired } })
      reviewed = true
      apply(result)
      if (status === 'APPROVED') await submitEmployeeEmails()
      else setNotice(status === 'PENDING' ? 'Draft saved.' : 'Draft rejected.')
    } catch (error) {
      setError(error.message)
      if (status === 'APPROVED' && reviewed) await refreshDelivery()
    } finally { setBusy(false) }
  }
  async function submitEmployeeEmails() {
    const delivery = await api(`/decisions/${id}/send`, { method: 'POST' })
    apply(await api(`/decisions/${id}`))
    if (delivery.status === 'ACCEPTED') setNotice('Message approved. Employee emails accepted by the provider for delivery.')
    else setError(`Message approved, but employee email status is ${delivery.status}. Check the delivery record before trying again.`)
  }
  async function refreshDelivery() {
    try { apply(await api(`/decisions/${id}`)) } catch { /* The server also blocks duplicate submissions. */ }
  }
  async function checkDelivery() {
    setBusy(true); setError('')
    try {
      const delivery = await api(`/decisions/${id}/delivery-status`, { method: 'POST' })
      setDetail(current => ({ ...current, delivery }))
    } catch (error) { setError(error.message) } finally { setBusy(false) }
  }
  function download() {
    const recipients = detail.recipients.filter(row => row.included).map(row => row.email)
    const blob = new Blob([`${recipients.length ? `To: ${recipients.join(', ')}\n` : ''}Subject: ${detail.draft_subject}\n\n${detail.draft_body}`], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = `decision-${id}-${detail.manager_status.toLowerCase()}.txt`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  if (!detail) return <main className="container"><a className="back" href="#" onClick={onNavigate}>← Week plan</a><ErrorMessage>{error}</ErrorMessage><p role="status">{error ? 'Could not load this decision.' : 'Loading draft…'}</p></main>
  const zone = detail.alternatives.facility_snapshot?.timezone
  const group = detail.alternatives.message_group
  return <main className="container review-container"><a className="back" href="#" onClick={onNavigate}>← Week plan</a>
    <div className="page-head"><div><p className="eyebrow">{detail.alternatives.facility_snapshot?.name || `Facility ${detail.location_id}`}</p><h1>{group?.shift_name || 'Daily assessment'}</h1>
      <p className="muted">{when(detail.affected_start, zone)} – {when(detail.affected_end, zone, true)}</p></div><Badge tone={detail.manager_status.toLowerCase()}>{label(detail.manager_status)}</Badge></div>
    <nav className="review-steps" aria-label="Message review steps">{['Risk', 'Recipients', 'Message'].map((name, index) => <button key={name} disabled={busy} aria-current={step === index ? 'step' : undefined} onClick={() => changeStep(index)}><span>{index + 1}</span>{name}</button>)}</nav>
    <ErrorMessage>{error}</ErrorMessage>{notice && <div className="notice success" role="status">{notice}</div>}
    <section className="card review-panel" ref={stepPanel} tabIndex={-1} aria-busy={busy} aria-label={`${['Risk', 'Recipients', 'Message'][step]} review`}>
      {step === 0 && <RiskStep detail={detail} form={form} busy={busy} onEdit={edit} timezone={zone} onPosture={changePosture} />}
      {step === 1 && <RecipientStep rows={rows} selection={selection} busy={busy} detail={detail} timezone={zone} onPreview={preview} onToggle={toggle} />}
      {step === 2 && <MessageStep detail={detail} form={form} rows={rows} busy={busy} dirty={dirty} ready={ready} essential={essential} email={email} onEssential={setEssential} onEdit={edit} onSave={save} onDownload={download} onRefresh={refreshAssessment} refreshing={refreshing} onCheckDelivery={checkDelivery} />}
      <div className="step-actions">{step > 0 ? <button disabled={busy} onClick={() => changeStep(step - 1)}>← {step === 1 ? 'Risk' : 'Recipients'}</button> : <span className="help">You choose the final action.</span>}
        {step < 2 && <button className="primary" disabled={busy || !ready} onClick={() => changeStep(step + 1)}>{busy ? 'Updating recipients…' : `Continue to ${step === 0 ? 'recipients' : 'message'} →`}</button>}</div>
    </section><ReviewHistory detail={detail} timezone={zone} />
  </main>
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(sessionStorage.getItem('facility_token')))
  const [decisionId, setDecisionId] = useState(decisionFromHash())
  const [dirty, setDirty] = useState(false)
  const [account, setAccount] = useState(null)
  const dirtyRef = useRef(false)
  const currentHash = useRef(window.location.hash)
  const onDirtyChange = useCallback(value => { dirtyRef.current = value; setDirty(value) }, [])
  useEffect(() => {
    let active = true
    if (loggedIn) api('/me').then(value => { if (active) setAccount(value) }).catch(() => {})
    else setAccount(null)
    return () => { active = false }
  }, [loggedIn])
  function canLeave() {
    if (dirtyRef.current && !window.confirm('Discard your unsaved draft changes?')) return false
    dirtyRef.current = false
    return true
  }
  function onNavigate(event) { if (!canLeave()) event.preventDefault() }
  function logout() { if (!canLeave()) return; sessionStorage.removeItem('facility_token'); sessionStorage.removeItem('facility_manager'); setDirty(false); setLoggedIn(false); window.location.hash = '' }
  useEffect(() => {
    const navigate = () => {
      if (!canLeave()) { window.history.replaceState(null, '', currentHash.current || '#'); return }
      currentHash.current = window.location.hash
      setDirty(false); setDecisionId(decisionFromHash()); window.scrollTo({ top: 0 })
    }
    const expired = () => { sessionStorage.removeItem('facility_token'); sessionStorage.removeItem('facility_manager'); dirtyRef.current = false; setDirty(false); setLoggedIn(false); window.location.hash = '' }
    window.addEventListener('hashchange', navigate); window.addEventListener('session-expired', expired)
    return () => { window.removeEventListener('hashchange', navigate); window.removeEventListener('session-expired', expired) }
  }, [])
  useEffect(() => {
    if (!dirty) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  if (!loggedIn) return <Login onLogin={() => setLoggedIn(true)} />
  return <><header className="app-header"><a className="brand" href="#" onClick={onNavigate}><span className="brand-mark">F</span>Facility Operations</a>
    <div className="inline-actions"><span className="draft-mode">Manager approval required</span><span className="manager-name">{account?.name || sessionStorage.getItem('facility_manager')}{account && <small>Manager ID: {account.emp_id} · {account.email}</small>}</span><button className="small-button" onClick={logout}>Sign out</button></div></header>
    {decisionId ? <DecisionDetails key={decisionId} id={decisionId} onDirtyChange={onDirtyChange} onNavigate={onNavigate} /> : <Dashboard />}
    <footer>Manager-reviewed recommendations · Demo policy · Not an emergency warning service</footer></>
}
