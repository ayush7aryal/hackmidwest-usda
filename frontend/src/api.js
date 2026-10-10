// All calls use the same origin; Nginx and Vite proxy /api to FastAPI.
export async function api(path, { method = 'GET', body } = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 180000)
  const token = sessionStorage.getItem('facility_token')
  try {
    const response = await fetch(`/api${path}`, {
      method,
      headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401 && path !== '/login') window.dispatchEvent(new Event('session-expired'))
      const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('; ') : data.detail
      throw new Error(detail || `Request failed (${response.status}). Please retry.`)
    }
    return data
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Evaluation timed out. Refresh history before retrying; it may still be running.')
    throw error
  } finally {
    clearTimeout(timeout)
  }
}
