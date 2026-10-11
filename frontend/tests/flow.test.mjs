import assert from 'node:assert/strict'
import { afterEach, beforeEach, test } from 'node:test'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import React, { act } from 'react'
import { build } from 'esbuild'
import { JSDOM } from 'jsdom'

// Exercise the actual app with DOM events and synthetic HTTP responses. No live
// source/model requests, email delivery, or browser automation are involved.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost:8080/' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, Element: dom.window.Element, sessionStorage: dom.window.sessionStorage, IS_REACT_ACT_ENVIRONMENT: true })
window.scrollTo = () => {}
window.confirm = () => false
const { createRoot } = await import('react-dom/client')
const bundle = await build({ stdin: { contents: await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8'), resolveDir: new URL('../src/', import.meta.url).pathname, loader: 'jsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'], loader: { '.css': 'empty' } })
const module = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const App = module.exports.default
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
let root, fixture, requests, failPreview, failWeek, failAlerts, downloadBlob, downloadName
let emailReady, sendStatus, failReview

function employee(id, name, included, reason, essential = false, remote = false) {
  return { emp_id: id, name, email: `${id}@example.com`, location_id: 1, shift_id: 1, shift_name: 'Weekday early',
    is_manager: false, is_essential: essential, is_teleworking: remote, weekly_hours: { wed: ['08:00', '16:00'], thu: ['08:00', '16:00'] },
    included, reason, requires_manager_review: included && essential }
}

function makeFixture() {
  const rows = [employee(1101, 'Casey Demo', true, 'scheduled_on_site'), employee(1104, 'Avery Demo', true, 'scheduled_on_site', true),
    employee(1103, 'Riley Remote', false, 'already_remote', false, true), employee(1106, 'Robin Off', false, 'not_scheduled')]
  rows[3].weekly_hours.wed = null
  const days = Array.from({ length: 7 }, (_, i) => ({ date: `2026-10-${12 + i}`, state: 'COMPLETED', posture: 'NORMAL', reason: `Synthetic day ${i + 1}: no automatic schedule change.`, warnings: [], decision_ids: [100 + i], message_groups: [] }))
  days[2] = { ...days[2], posture: 'TELEWORK', reason: 'Synthetic Wednesday storm affects on-site shifts. Review each draft.', decision_ids: [14, 15], message_groups: [
    { decision_id: 14, shift_id: 1, shift_name: 'Weekday early', start: '2026-10-14T13:00:00Z', end: '2026-10-14T21:00:00Z', posture: 'TELEWORK', recipient_count: 2, manager_status: 'PENDING' },
    { decision_id: 15, shift_id: 2, shift_name: 'Weekday late', start: '2026-10-14T15:00:00Z', end: '2026-10-14T23:00:00Z', posture: 'TELEWORK', recipient_count: 2, manager_status: 'PENDING' }] }
  days[3].warnings = ['Modeled air-quality coverage is incomplete.']
  days[4] = { date: days[4].date, state: 'INSUFFICIENT_DATA', posture: null, reason: 'Synthetic weather coverage unavailable.', warnings: [], decision_ids: [], message_groups: [] }
  const alerts = [
    { event: 'Synthetic Tornado Warning', severity: 'Extreme', status: 'Actual', messageType: 'Alert', instruction: 'SYNTHETIC: Take shelter immediately.', description: 'Synthetic tornado description.', onset: '2026-10-14T13:00:00Z', ends: '2026-10-14T21:00:00Z', url: 'https://api.weather.gov/alerts/test-1' },
    { event: 'Synthetic Surf Advisory', severity: 'Minor', status: 'Actual', messageType: 'Alert', instruction: 'SYNTHETIC: Avoid the surf.', description: 'Synthetic surf description.', onset: '2026-10-14T13:00:00Z', ends: '2026-10-14T21:00:00Z', url: 'https://api.weather.gov/alerts/test-2' }]
  const office = { id: 1, name: 'Synthetic Test Facility', latitude: 41.8, longitude: -87.6, timezone: 'America/Chicago', open_time: '08:00', close_time: '18:00', last_checked_at: null, status: 'NORMAL' }
  const decision = { id: 14, location_id: 1, evaluated_at: '2026-10-10T19:00:00Z', affected_start: '2026-10-14T13:00:00Z', affected_end: '2026-10-14T21:00:00Z', posture: 'TELEWORK', confidence: .5,
    reason: 'Synthetic Wednesday storm affects this dated early shift. Manager review required.', manager_status: 'PENDING', draft_subject: 'Synthetic Wednesday early shift', draft_body: 'DRAFT — synthetic manager-reviewed shift instructions.', recipients: rows, evidence: [], thresholds: [],
    alternatives: { facility_snapshot: office, message_group: days[2].message_groups[0], warnings: ['Synthetic forecast requires review.'], inputs: { weather: { alerts } }, original_recommendation: { posture: 'TELEWORK', reason: 'Synthetic original advice.' } }, sent_at: null }
  return { office, decision, alerts, week: { state: 'COMPLETED', evaluated_at: '2026-10-10T19:00:00Z', message: 'Synthetic weekly check', days } }
}

function previewRows(body) {
  return fixture.decision.recipients.map(row => {
    const eligible = body.scope === 'all_staff' || ((body.posture !== 'NORMAL' || body.posture !== fixture.decision.alternatives.original_recommendation.posture) && !row.is_teleworking && row.weekly_hours.wed)
    const blocked = ['already_notified', 'delivery_uncertain'].includes(row.reason)
    const included = Boolean(eligible && !blocked && !body.exclude_ids?.includes(row.emp_id))
    return { ...row, included, requires_manager_review: included && row.is_essential,
      reason: blocked ? row.reason : body.exclude_ids?.includes(row.emp_id) ? 'manager_excluded' : included ? 'scheduled_on_site' : row.is_teleworking ? 'already_remote' : 'not_scheduled' }
  })
}

beforeEach(() => {
  fixture = makeFixture(); requests = []; failPreview = false; failWeek = false; failAlerts = false; downloadBlob = null; downloadName = null
  emailReady = true; sendStatus = 'ACCEPTED'; failReview = false
  window.history.replaceState(null, '', '/')
  sessionStorage.clear(); sessionStorage.setItem('facility_token', 'synthetic-token'); sessionStorage.setItem('facility_manager', 'Synthetic Manager')
  document.body.innerHTML = '<div id="root"></div>'
  URL.createObjectURL = blob => { downloadBlob = blob; return 'blob:synthetic-export' }
  URL.revokeObjectURL = () => {}
  window.HTMLAnchorElement.prototype.click = function () { downloadName = this.download }
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined
    requests.push({ path, method: options.method || 'GET', body })
    if (path === '/api/me') return response({ emp_id: 1001, name: 'Synthetic Manager', email: 'manager@example.com', location_id: 1 })
    if (path === '/api/email-status') return response({ ready: emailReady, provider: 'sendgrid', demo_redirect: true })
    if (path === '/api/locations' && !body) return response([fixture.office])
    if (path === '/api/locations' && options.method === 'POST') { fixture.office = { ...fixture.office, ...body }; fixture.week = null; return response(fixture.office) }
    if (String(path).startsWith('https://geocoding-api.open-meteo.com/')) return response({ results: [{ id: 1, name: 'Panama City Beach', admin1: 'Florida', country: 'United States', latitude: 30.1766, longitude: -85.8055, timezone: 'America/Chicago' }] })
    if (path === `/api/locations/${fixture.office.id}/decisions`) return response([fixture.decision])
    if (path === `/api/locations/${fixture.office.id}/week-plan`) return response(fixture.week)
    if (path === `/api/locations/${fixture.office.id}/weather-alerts`) return response(failAlerts ? { available: false, alerts: [], error: 'Synthetic alert outage' } : { available: true, alerts: fixture.alerts, fetched_at: '2026-10-10T19:00:00Z' })
    if (path === '/api/locations/1/evaluate-week') return response(failWeek ? { state: 'INSUFFICIENT_DATA', message: 'Synthetic model outage', days: [] } : fixture.week)
    if (path === '/api/decisions/14' && !body) return response(fixture.decision)
    if (path === '/api/decisions/14/recipients') return failPreview ? response({ detail: 'Synthetic recipient outage' }, 503) : response(previewRows(body))
    if (path === '/api/decisions/14/send') {
      fixture.decision = { ...fixture.decision, sent_at: sendStatus === 'ACCEPTED' ? '2026-10-10T20:00:00Z' : null, delivery: { status: sendStatus, provider: 'sendgrid' } }
      return response(fixture.decision.delivery)
    }
    if (path === '/api/decisions/14/review') {
      if (failReview) return response({ detail: 'Staff selection changed; review again.' }, 409)
      if (body.status === 'APPROVED' && !body.essential_confirmed) return response({ detail: 'Essential confirmation required' }, 422)
      fixture.decision = { ...fixture.decision, ...body, manager_status: body.status, recipients: previewRows(body), alternatives: { ...fixture.decision.alternatives, recipient_selection: { scope: body.scope, include_ids: body.include_ids, exclude_ids: body.exclude_ids } } }
      return response(fixture.decision)
    }
    if (path === '/api/locations/1' && options.method === 'PATCH') { fixture.office = { ...fixture.office, ...body }; fixture.week = null; fixture.alerts = []; return response(fixture.office) }
    throw new Error(`Unexpected test request: ${path}`)
  }
  root = createRoot(document.getElementById('root'))
})

afterEach(async () => { await act(async () => root.unmount()) })
const tick = () => new Promise(resolve => setImmediate(resolve))
async function mount(hash = '') { window.history.replaceState(null, '', `/${hash}`); await act(async () => { root.render(React.createElement(App)); await tick() }) }
function button(name) { const found = [...document.querySelectorAll('button')].find(item => typeof name === 'string' ? item.textContent.trim() === name : name.test(item.textContent)); assert(found, `Missing button: ${name}`); return found }
async function click(element) { await act(async () => { element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); await tick() }) }
async function change(element, value) {
  await act(async () => {
    if (element.tagName === 'SELECT') { element.value = value; element.dispatchEvent(new window.Event('change', { bubbles: true })) }
    else { Object.getOwnPropertyDescriptor(element.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype, 'value').set.call(element, value); element.dispatchEvent(new window.Event('input', { bubbles: true })) }
    await tick()
  })
}
function outsideClosedDetails(element) { for (let node = element.parentElement; node; node = node.parentElement) if (node.tagName === 'DETAILS' && !node.open) return false; return true }

// A compact dashboard must still surface exceptions and keep evidence reachable.
test('week selector opens the first pending day and shows only one date detail', async () => {
  await mount()
  assert.equal(document.querySelectorAll('.day-button').length, 7)
  assert.equal(document.querySelector('[aria-pressed=true].day-button').getAttribute('aria-label'), 'Wednesday, Oct 14')
  assert.equal(document.querySelectorAll('.selected-day').length, 1)
  assert.equal(document.querySelectorAll('.shift-draft').length, 2)
  assert.equal(document.querySelector('.history-panel').open, false)
  await click(document.querySelector('[aria-label="Monday, Oct 12"]'))
  assert.match(document.querySelector('.selected-day').textContent, /Synthetic day 1/)
  assert.equal(document.querySelectorAll('.shift-draft').length, 0)
  assert.equal(document.querySelector('.selected-day').textContent.includes('Synthetic Wednesday storm'), false)
})

test('coverage gaps and source notes remain visible without showing every explanation', async () => {
  await mount()
  assert.match(document.querySelector('.week-plan').textContent, /1 day unavailable/)
  await click(document.querySelector('[aria-label="Thursday, Oct 15"]'))
  const notes = document.querySelector('.selected-day .source-notes')
  assert.match(notes.querySelector('summary').textContent, /Limited forecast coverage/)
  assert.equal(notes.open, false)
  await click(notes.querySelector('summary'))
  assert.equal(notes.open, true)
  await click(document.querySelector('[aria-label="Friday, Oct 16"]'))
  assert.match(document.querySelector('.selected-day').textContent, /No all-clear or employee draft/)
  assert.equal(document.querySelectorAll('.selected-day a').length, 0)
})

test('urgent instructions stay visible and advisory details can be expanded', async () => {
  await mount()
  const alerts = [...document.querySelectorAll('.alert-item')]
  assert.equal(outsideClosedDetails(alerts[0].querySelector('.alert-description')), true)
  assert.match(alerts[0].textContent, /Take shelter immediately/)
  const advisory = alerts[1].querySelector('details')
  assert.equal(advisory.open, false)
  await click(advisory.querySelector('summary'))
  assert.equal(advisory.open, true)
  assert.match(advisory.textContent, /Avoid the surf/)
})

test('failed weekly refresh retains the saved plan and labels it as a previous check', async () => {
  await mount(); failWeek = true
  await click(button('Check next 7 days ↗'))
  assert.match(document.querySelector('[role=alert]').textContent, /Showing the previous weekly check/)
  assert.equal(document.querySelectorAll('.day-button').length, 7)
  assert.equal(document.querySelectorAll('.shift-draft').length, 2)
})

test('location editing has its own focused form and preserves coordinate/ID editing', async () => {
  await mount(); await click(button('Edit location'))
  assert.equal(document.querySelector('.week-plan'), null)
  const inputs = document.querySelectorAll('.form-grid input')
  assert.equal(inputs.length, 6)
  const named = title => [...document.querySelectorAll('label')].find(item => item.textContent === title).querySelector('input')
  await change(named('Location ID'), '9'); await change(named('Latitude'), '30.1766'); await change(named('Longitude'), '-85.8055')
  await click(button('Save facility'))
  const saved = requests.find(item => item.method === 'PATCH')
  assert.equal(saved.body.id, 9); assert.equal(saved.body.latitude, 30.1766); assert.equal(saved.body.longitude, -85.8055)
  assert(document.querySelector('.empty-plan'))
  assert.equal(document.querySelector('[role=alert]'), null)
})

test('three-step review keeps excluded staff available and shows only affected-day hours', async () => {
  await mount('#decision/14')
  assert.equal(document.querySelector('h2').textContent, 'Review the risk')
  assert.equal(document.querySelector('.message-input'), null)
  assert.equal(document.querySelector('.disclosure').open, false)
  await click(button('Continue to recipients →'))
  assert.equal(document.querySelectorAll('tbody tr').length, 2)
  assert.match(document.querySelector('tbody').textContent, /wed 08:00–16:00/)
  assert.equal(document.querySelector('tbody').textContent.includes('thu'), false)
  await click(button('Excluded (2)'))
  assert.match(document.querySelector('tbody').textContent, /Riley Remote/)
  assert.equal(document.querySelector('[aria-label="Include Riley Remote"]').disabled, true)
  await change(document.querySelector('select'), 'all_staff')
  assert.equal(document.querySelectorAll('tbody tr').length, 4)
  assert.equal(document.querySelector('[aria-label="Include Riley Remote"]').disabled, false)
})

test('essential confirmation gates approval and saved export includes the actual recipient list', async () => {
  await mount('#decision/14'); await click(button('Continue to recipients →')); await click(button('Continue to message →'))
  assert.equal(document.querySelector('.message-input') !== null, true)
  assert.equal(button('Approve and send email').disabled, true)
  await click(document.querySelector('.check-notice input'))
  assert.equal(button('Approve and send email').disabled, false)
  await change(document.querySelector('.message-input'), 'SYNTHETIC: Manager-approved Wednesday shift instructions.')
  assert.equal(button('Download saved message ↓').disabled, true)
  await click(button('Approve and send email'))
  const saved = requests.find(item => item.path.endsWith('/review'))
  assert.equal(saved.body.status, 'APPROVED'); assert.equal(saved.body.essential_confirmed, true)
  assert.equal(saved.body.draft_body, 'SYNTHETIC: Manager-approved Wednesday shift instructions.')
  assert.match(document.querySelector('.success').textContent, /Message approved/)
  await click(button('Download saved message ↓'))
  assert.match(await downloadBlob.text(), /To: 1101@example.com, 1104@example.com/)
  assert.equal(downloadName, 'decision-14-approved.txt')
  assert.equal(requests.filter(item => item.path.endsWith('/send')).length, 1)
})

test('recipient refresh failure blocks approval until a successful retry', async () => {
  await mount('#decision/14'); failPreview = true
  await click(button('Continue to recipients →'))
  assert.match(document.querySelector('[role=alert]').textContent, /Synthetic recipient outage/)
  await click(button('3Message'))
  assert.equal(button('Approve and send email').matches(':disabled'), true)
  failPreview = false
  await click(button('2Recipients')); await click(button('Refresh staff')); await click(button('Continue to message →'))
  await click(document.querySelector('.check-notice input'))
  assert.equal(button('Approve and send email').matches(':disabled'), false)
})

test('unsaved edits can cancel navigation and recipient exclusions survive the next step', async () => {
  await mount('#decision/14'); await click(button('Continue to recipients →'))
  await click(document.querySelector('[aria-label="Include Casey Demo"]'))
  assert.equal(button('Selected (1)') !== null, true)
  await click(button('Continue to message →'))
  assert.match(document.querySelector('.review-panel').textContent, /1 recipient/)
  const nav = new window.MouseEvent('click', { bubbles: true, cancelable: true })
  await act(async () => document.querySelector('.back').dispatchEvent(nav))
  assert.equal(nav.defaultPrevented, true)
  await click(button('Save draft'))
  const saved = requests.find(item => item.path.endsWith('/review'))
  assert.deepEqual(saved.body.exclude_ids, [1101])
})

test('reading steps does not mark a draft dirty and browser navigation protects actual edits', async () => {
  await mount('#decision/14'); await click(button('Continue to recipients →')); await click(button('Continue to message →'))
  assert.equal(button('Download saved message ↓').disabled, false)
  await change(document.querySelector('.message-input'), 'SYNTHETIC: Unsaved manager instructions.')
  window.history.pushState(null, '', '/#')
  await act(async () => { window.dispatchEvent(new window.HashChangeEvent('hashchange')); await tick() })
  assert.equal(window.location.hash, '#decision/14')
  assert(document.querySelector('.message-input'))
  assert.equal(document.querySelector('.message-input').value, 'SYNTHETIC: Unsaved manager instructions.')
})

test('mixed-action days show the actual action on each shift draft', async () => {
  fixture.week.days[2].message_groups[0].posture = 'DELAYED_OPENING'
  fixture.week.days[2].message_groups[1].posture = 'EARLY_DISMISSAL'
  await mount()
  assert.match(document.querySelector('.selected-day').textContent, /Multiple shift actions/)
  const drafts = document.querySelectorAll('.shift-draft')
  assert.match(drafts[0].textContent, /Delayed opening/)
  assert.match(drafts[1].textContent, /Early dismissal/)
})

test('alert outages keep previous instructions visible and explicitly mark them outdated', async () => {
  await mount(); failAlerts = true
  await click(document.querySelector('.weather-alerts button'))
  assert.match(document.querySelector('.weather-alerts [role=alert]').textContent, /Saved alerts below may be outdated/)
  assert.equal(document.querySelectorAll('.alert-item').length, 2)
  assert.match(document.querySelector('.weather-alerts').textContent, /Take shelter immediately/)
})

test('changing the action recomputes recipients before the next step', async () => {
  await mount('#decision/14')
  await change(document.querySelector('select'), 'NORMAL')
  assert.equal(requests.find(item => item.path.endsWith('/recipients')).body.posture, 'NORMAL')
  await click(button('Continue to recipients →'))
  assert(button('Selected (2)'))
  await click(button('Continue to message →'))
  assert.match(document.querySelector('.review-panel').textContent, /2 recipients/)
  assert.match(document.querySelector('.review-panel').textContent, /Your action changed/)
})

test('canceled, test and unknown alert statuses stay visible without presenting old instructions as urgent', async () => {
  fixture.alerts[0].messageType = 'Cancel'
  fixture.alerts[1].status = 'Test'
  fixture.alerts.push({ ...fixture.alerts[0], event: 'Synthetic Unknown Alert', url: 'https://api.weather.gov/alerts/test-3', messageType: 'Alert', status: null })
  await mount()
  const alerts = document.querySelector('.weather-alerts')
  assert.match(alerts.textContent, /Canceled/); assert.match(alerts.textContent, /Test/); assert.match(alerts.textContent, /Status unknown/)
  assert.equal(alerts.querySelectorAll('.alert-description[role=alert]').length, 0)
  assert.equal(alerts.querySelectorAll('.alert-item').length, 3)
})

const field = title => [...document.querySelectorAll('label')].find(item => item.textContent === title).querySelector('input')

test('new facility supports name, coordinate search, pin selection and existing manual fields', async () => {
  await mount(); await click(button('+ Add facility'))
  assert(document.querySelector('.leaflet-container'))
  assert.equal(document.querySelectorAll('.leaflet-marker-icon').length, 0)
  await change(field('Facility name'), 'Panama Demo Facility')
  await change(field('Place name or coordinates'), '30.1766, -85.8055')
  await click(button('Find on map'))
  assert.equal(field('Latitude').value, '30.1766'); assert.equal(field('Longitude').value, '-85.8055')
  assert.equal(document.querySelectorAll('.leaflet-marker-icon').length, 1)
  assert.equal(requests.some(item => String(item.path).startsWith('https:')), false)
  // Exercise Leaflet's actual click handler; map gestures update the same form fields.
  await act(async () => { document.querySelector('.leaflet-container').dispatchEvent(new window.MouseEvent('click', { bubbles: true, clientX: 30, clientY: 20 })); await tick() })
  assert.notEqual(field('Latitude').value, '30.1766')
  assert(Number.isFinite(Number(field('Latitude').value)))
  await change(field('Latitude'), '30.1766'); await change(field('Longitude'), '-85.8055')
  await change(field('Location ID'), '9'); await click(button('Save facility'))
  const saved = requests.find(item => item.method === 'POST' && item.path === '/api/locations')
  assert.equal(saved.body.name, 'Panama Demo Facility'); assert.equal(saved.body.latitude, 30.1766)
  assert.equal(saved.body.longitude, -85.8055); assert.equal(saved.body.id, 9)
})

test('place search requires a result selection and does not replace the facility name', async () => {
  await mount(); await click(button('Edit location'))
  await change(field('Place name or coordinates'), 'Panama City Beach, FL')
  await click(button('Find on map'))
  assert.equal(field('Latitude').value, '41.8')
  assert(document.querySelector('[aria-label="Place search results"]'))
  await click(document.querySelector('.map-results button'))
  assert.equal(field('Latitude').value, '30.1766'); assert.equal(field('Longitude').value, '-85.8055')
  assert.equal(field('Facility name').value, 'Synthetic Test Facility')
  assert.equal(field('Timezone').value, 'America/Chicago')
  assert.equal(document.querySelector('.map-results'), null)
})

test('invalid coordinate searches, no matches and search outages preserve manual editing', async () => {
  await mount(); await click(button('Edit location'))
  await change(field('Place name or coordinates'), '91, -85')
  await click(button('Find on map'))
  assert.match(document.querySelector('.facility-map-section [role=status]').textContent, /valid latitude/)
  assert.equal(field('Latitude').value, '41.8')
  assert.equal(requests.some(item => String(item.path).startsWith('https:')), false)
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (path, options) => String(path).startsWith('https:') ? response({}) : originalFetch(path, options)
  await change(field('Place name or coordinates'), 'Unknown location'); await click(button('Find on map'))
  assert.match(document.querySelector('.facility-map-section [role=status]').textContent, /No places found/)
  globalThis.fetch = async (path, options) => { if (String(path).startsWith('https:')) throw new Error('Synthetic search outage'); return originalFetch(path, options) }
  await click(button('Find on map'))
  assert.match(document.querySelector('.facility-map-section [role=status]').textContent, /search unavailable/)
  await change(field('Latitude'), ''); assert.equal(document.querySelectorAll('.leaflet-marker-icon').length, 0)
  await change(field('Latitude'), '30'); assert.equal(document.querySelectorAll('.leaflet-marker-icon').length, 1)
})

test('evaluation displays an accessible loader and prevents duplicate requests until completion', async () => {
  await mount()
  const originalFetch = globalThis.fetch
  let complete
  globalThis.fetch = (path, options) => path === '/api/locations/1/evaluate-week' ? new Promise(resolve => { complete = () => resolve(response(fixture.week)) }) : originalFetch(path, options)
  await click(button('Check next 7 days ↗'))
  const loading = document.querySelector('.evaluation-progress[role=status]')
  assert(loading.querySelector('.spinner'))
  assert.match(loading.textContent, /Checking the next 7 days/)
  assert.equal(button('Checking the week…').disabled, true)
  assert.equal(button('Evaluate today').disabled, true)
  assert.equal(button('Edit location').disabled, true)
  await act(async () => { complete(); await tick() })
  assert.equal(document.querySelector('.evaluation-progress'), null)
  assert.equal(button('Check next 7 days ↗').disabled, false)
})

test('operational action is highlighted only in the Risk review panel', async () => {
  await mount('#decision/14')
  assert(document.querySelector('.operational-action select'))
  await click(button('3Message'))
  assert.equal(document.querySelector('.operational-action'), null)
  assert.equal(document.querySelector('.review-panel').textContent.includes('Work remotely'), false)
  assert.equal(document.querySelector('.review-panel').textContent.includes('Approval saves the draft'), false)
})

test('five additional managers are available at login with their demo cities', async () => {
  sessionStorage.clear()
  await mount()
  const options = [...document.querySelectorAll('select option')]
  assert.equal(options.length, 7)
  assert(options.some(option => option.value === '52001' && option.textContent.includes('Jacksonville')))
  assert(options.some(option => option.value === '53001' && option.textContent.includes('Charleston')))
})

test('normal operations leave recipients unchecked and a manager override resets exclusions', async () => {
  fixture.decision.posture = 'NORMAL'
  fixture.decision.alternatives.original_recommendation.posture = 'NORMAL'
  fixture.decision.recipients = fixture.decision.recipients.map(row => ({ ...row, included: false, requires_manager_review: false, reason: 'normal_operations' }))
  fixture.decision.draft_body = 'Recommended action: NORMAL\nSynthetic instructions.'
  await mount('#decision/14')
  await click(button('Continue to recipients →'))
  assert(button('Selected (0)'))
  await click(button('1Risk'))
  await change(document.querySelector('select'), 'CLOSED')
  const request = requests.filter(item => item.path.endsWith('/recipients')).at(-1)
  assert.deepEqual(request.body.exclude_ids, [])
  assert.equal(request.body.posture, 'CLOSED')
  await click(button('Continue to recipients →'))
  assert(button('Selected (2)'))
  await click(button('Continue to message →'))
  assert.match(document.querySelector('.message-input').value, /Recommended action: CLOSED/)
  assert.equal(button('Approve and send email').disabled, true)
})

test('approve and send submits review first, sends once and blocks repeat sending', async () => {
  await mount('#decision/14'); await click(button('Continue to recipients →')); await click(button('Continue to message →'))
  assert.equal(requests.filter(item => item.path.endsWith('/send')).length, 0)
  await click(document.querySelector('.check-notice input'))
  await click(button('Approve and send email'))
  assert.equal(fixture.decision.manager_status, 'APPROVED')
  const actions = requests.filter(item => /\/(review|send)$/.test(item.path))
  assert.deepEqual(actions.map(item => item.path), ['/api/decisions/14/review', '/api/decisions/14/send'])
  assert.equal(button('Send employee emails').disabled, true)
  assert.match(document.querySelector('.success').textContent, /accepted by the provider/)
  assert.equal(button('Download saved message ↓').disabled, false)
  await click(button('Download saved message ↓'))
  assert.equal(downloadName, 'decision-14-approved.txt')
  assert(downloadBlob)
})

test('manager account and review email show the correct login ID and destination', async () => {
  fixture.decision.alternatives.manager_alert = { status: 'ACCEPTED', template: {
    to: ['manager@example.com'], subject: 'Please review: Synthetic facility',
    body: 'Hello Synthetic Manager,\nManager ID (login): 1001 | manager@example.com\nPlease review the risk.' } }
  await mount('#decision/14')
  assert.match(document.querySelector('.manager-name').textContent, /Manager ID: 1001 · manager@example.com/)
  const email = document.querySelector('.manager-review-email')
  assert.equal(email.open, false)
  assert.match(email.textContent, /Please review: Synthetic facility/)
  assert.match(email.textContent, /manager@example.com/)
})

test('failed approval never attempts employee delivery', async () => {
  failReview = true
  await mount('#decision/14'); await click(button('3Message')); await click(document.querySelector('.check-notice input'))
  await click(button('Approve and send email'))
  assert.equal(requests.filter(item => item.path.endsWith('/send')).length, 0)
  assert.equal(fixture.decision.manager_status, 'PENDING')
  assert.match(document.querySelector('[role=alert]').textContent, /Staff selection changed/)
})

test('uncertain combined sending shows a failure and disables duplicate submission', async () => {
  sendStatus = 'UNKNOWN'
  await mount('#decision/14'); await click(button('3Message')); await click(document.querySelector('.check-notice input'))
  await click(button('Approve and send email'))
  assert.equal(fixture.decision.manager_status, 'APPROVED')
  assert.match(document.querySelector('[role=alert]').textContent, /email status is UNKNOWN/)
  assert.equal(button('Approve and send email').matches(':disabled'), true)
  assert.equal(button('Send employee emails').disabled, true)
  assert.equal(requests.filter(item => item.path.endsWith('/send')).length, 1)
})

test('unconfigured email or no affected employees disables combined sending', async () => {
  emailReady = false
  await mount('#decision/14'); await click(button('3Message')); await click(document.querySelector('.check-notice input'))
  assert.equal(button('Approve and send email').disabled, true)
  assert.match(document.querySelector('.review-panel').textContent, /Email sending is unavailable/)
  assert.equal(button('Save draft').matches(':disabled'), false)
})

test('changing an action reselects a previously excluded affected employee', async () => {
  await mount('#decision/14'); await click(button('Continue to recipients →'))
  await click(document.querySelector('[aria-label="Include Casey Demo"]'))
  assert(button('Selected (1)'))
  await click(button('1Risk'))
  await change(document.querySelector('select'), 'CLOSED')
  await click(button('Continue to recipients →'))
  assert(button('Selected (2)'))
})

test('refreshing legacy unchanged recommendations checks affected employees without changing action', async () => {
  fixture.decision.alternatives.notification = { required: false, reason: 'unchanged_operation' }
  fixture.decision.recipients = fixture.decision.recipients.map(row => row.included
    ? { ...row, included: false, reason: 'unchanged_operation', requires_manager_review: false } : row)
  await mount('#decision/14'); await click(button('2Recipients'))
  assert(button('Selected (2)'))
  assert(document.querySelector('[aria-label="Include Casey Demo"]').checked)
  assert(document.querySelector('[aria-label="Include Avery Demo"]').checked)
  assert.equal(requests.filter(item => item.path.endsWith('/recipients')).at(-1).body.posture, 'TELEWORK')
  assert.equal(requests.filter(item => item.path.endsWith('/send')).length, 0)
})

test('select affected staff clears manual exclusions and keeps remote staff unchecked', async () => {
  await mount('#decision/14'); await click(button('2Recipients'))
  await click(document.querySelector('[aria-label="Include Casey Demo"]'))
  assert(button('Selected (1)'))
  await click(button('Select affected staff'))
  assert(button('Selected (2)'))
  assert(document.querySelector('[aria-label="Include Casey Demo"]').checked)
  assert.deepEqual(requests.filter(item => item.path.endsWith('/recipients')).at(-1).body.exclude_ids, [])
  await click(button('Excluded (2)'))
  assert.equal(document.querySelector('[aria-label="Include Riley Remote"]').checked, false)
  assert.equal(document.querySelector('[aria-label="Include Riley Remote"]').disabled, true)
})

test('submitted and unresolved recipients cannot be rechecked through a wider audience', async () => {
  fixture.decision.recipients[0] = { ...fixture.decision.recipients[0], included: false, reason: 'already_notified' }
  fixture.decision.recipients[1] = { ...fixture.decision.recipients[1], included: false, reason: 'delivery_uncertain' }
  await mount('#decision/14'); await click(button('2Recipients'))
  await change(document.querySelector('select'), 'all_staff')
  await click(button('Excluded (2)'))
  assert.equal(document.querySelector('[aria-label="Include Casey Demo"]').disabled, true)
  assert.equal(document.querySelector('[aria-label="Include Avery Demo"]').disabled, true)
})
