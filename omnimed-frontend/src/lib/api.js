import { supabase } from './supabase'

// Backend base URL. Set VITE_API_URL in production (e.g. the Render URL);
// falls back to the local Flask dev server.
export const API = import.meta.env.VITE_API_URL || 'http://localhost:5000'

/**
 * fetch wrapper that attaches the current Supabase access token as a
 * Bearer header. Use for all calls to protected backend routes.
 */
export async function authedFetch(path, options = {}) {
  const { data } = await supabase.auth.getSession()
  const token = data?.session?.access_token

  const headers = { ...(options.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`

  return fetch(`${API}${path}`, { ...options, headers })
}

/** JSON POST to a protected route. Returns parsed JSON (throws on network error). */
export async function postJSON(path, body, signal) {
  const res = await authedFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  return res.json()
}

/**
 * Stream /ask over Server-Sent Events, consumed via fetch + ReadableStream
 * (EventSource can't send the Authorization header). Calls onModel(parsed)
 * for each model result as it arrives. onError is for transport/auth-level
 * failures only — per-model failures arrive as normal onModel events with
 * status "error". AbortError propagates to the caller.
 */
export async function askStream(body, { signal, onModel, onError } = {}) {
  const res = await authedFetch('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  })

  if (res.status === 401) { onError?.({ status: 401 }); return }
  if (!res.ok || !res.body) { onError?.({ status: res.status }); return }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sep
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)

      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!data) continue

      let parsed
      try { parsed = JSON.parse(data) } catch { continue }

      if (event === 'model') onModel?.(parsed)
      else if (event === 'error') onError?.(parsed)
      // 'end' is informational — the read loop ends naturally
    }
  }
}
