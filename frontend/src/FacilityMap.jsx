import React, { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export function coordinates(latitude, longitude) {
  if (latitude == null || longitude == null || String(latitude).trim() === '' || String(longitude).trim() === '') return null
  const lat = Number(latitude), lng = Number(longitude)
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lat, lng] : null
}

// Coordinates are resolved locally; only explicit place-name searches use geocoding.
export function searchCoordinates(query) {
  const parts = query.trim().split(/[\s,]+/)
  return parts.length === 2 ? coordinates(parts[0], parts[1]) : null
}

export default function FacilityMap({ latitude, longitude, onPick }) {
  const container = useRef(null), map = useRef(null), marker = useRef(null), pick = useRef(onPick), request = useRef(null)
  const [query, setQuery] = useState(''), [results, setResults] = useState([])
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [tileError, setTileError] = useState(false)
  pick.current = onPick
  useEffect(() => {
    const point = coordinates(latitude, longitude)
    const instance = L.map(container.current, { scrollWheelZoom: false }).setView(point || [39.5, -98.35], point ? 13 : 4)
    map.current = instance
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(instance)
    tiles.on('tileerror', () => setTileError(true))
    instance.on('click', event => pick.current({ latitude: Number(event.latlng.lat.toFixed(6)), longitude: Number(event.latlng.wrap().lng.toFixed(6)) }))
    return () => { request.current?.abort(); instance.remove(); map.current = null; marker.current = null }
  }, [])
  useEffect(() => {
    const point = coordinates(latitude, longitude)
    if (!map.current) return
    if (!point) { marker.current?.remove(); marker.current = null; return }
    if (!marker.current) {
      marker.current = L.marker(point, { draggable: true, icon: L.divIcon({ className: 'facility-pin', html: '<span></span>', iconSize: [24, 24], iconAnchor: [12, 12] }) }).addTo(map.current)
      marker.current.on('dragend', () => {
        const position = marker.current.getLatLng().wrap()
        pick.current({ latitude: Number(position.lat.toFixed(6)), longitude: Number(position.lng.toFixed(6)) })
      })
    } else marker.current.setLatLng(point)
    map.current.setView(point, Math.max(map.current.getZoom(), 13), { animate: false })
  }, [latitude, longitude])

  async function search() {
    const text = query.trim()
    if (busy || !text) return
    setResults([]); setMessage('')
    const point = searchCoordinates(text)
    if (point) { onPick({ latitude: point[0], longitude: point[1] }); setMessage('Pin updated. Refine it on the map if needed.'); return }
    if (/^[\d\s.,+\-]+$/.test(text)) { setMessage('Enter valid latitude, longitude (−90 to 90, −180 to 180).'); return }
    if (text.length < 2) { setMessage('Enter at least two characters for a place search.'); return }
    setBusy(true)
    const controller = new AbortController(); request.current = controller
    const timer = setTimeout(() => controller.abort(), 12000)
    try {
      const params = new URLSearchParams({ name: text, count: '5', language: 'en', format: 'json', countryCode: 'US' })
      const response = await fetch(`https://geocoding-api.open-meteo.com/v1/search?${params}`, { signal: controller.signal })
      if (!response.ok) throw new Error('Search unavailable')
      const data = await response.json()
      if (data.error) throw new Error('Search unavailable')
      const matches = (data.results || []).filter(item => coordinates(item.latitude, item.longitude))
      setResults(matches)
      if (!matches.length) setMessage('No places found. Try a nearby city or ZIP code, or enter coordinates.')
    } catch { setMessage('Place search unavailable. Try again or use the map and manual coordinates.') }
    finally { clearTimeout(timer); setBusy(false); request.current = null }
  }
  return <section className="facility-map-section" aria-label="Choose facility location">
    <h2>Find your location</h2><p className="help">Search a US city or ZIP code, or enter latitude, longitude. Click the map or drag the pin for the exact site.</p>
    <div className="map-search"><label>Place name or coordinates<input value={query} maxLength="200" placeholder="Panama City Beach, FL or 30.1766, -85.8055"
      onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); search() } }} /></label>
      <button type="button" disabled={busy || !query.trim()} onClick={search}>{busy ? 'Searching…' : 'Find on map'}</button></div>
    {results.length > 0 && <ul className="map-results" aria-label="Place search results">{results.map(item => <li key={item.id}><button type="button" onClick={() => {
      onPick({ latitude: item.latitude, longitude: item.longitude, ...(item.timezone ? { timezone: item.timezone } : {}) }); setResults([]); setMessage(`Pin set to ${item.name}. Check the timezone and refine the pin if needed.`)
    }}><strong>{item.name}</strong><span>{[item.admin1, item.country].filter(Boolean).join(', ')} · {item.latitude}, {item.longitude}</span></button></li>)}</ul>}
    {message && <p className="help" role="status">{message}</p>}
    <div ref={container} className="facility-map" aria-label="Interactive facility map" />
    {tileError && <p className="help" role="status">Some map tiles could not load. You can still search or enter coordinates below.</p>}
    <p className="help map-attribution">Place search: <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">Open-Meteo</a> / <a href="https://www.geonames.org/" target="_blank" rel="noreferrer">GeoNames</a></p>
  </section>
}
