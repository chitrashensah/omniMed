// Session-scoped store for Layer 1 citation-verification results.
//
// Each check used to live inside the button that ran it, which meant results
// could be read one answer at a time and never compared. Comparing models is
// the whole point of the project, and a per-model accuracy rate only exists
// once the results are pooled — so they are kept here instead, keyed by
// (message, model), and the scoreboard reads across them.
//
// Persisted per session so a reload does not throw away work that cost real
// requests to PubMed.

const KEY = (sessionId) => `omnimed_verify_${sessionId}`

const listeners = new Set()
let current = { sessionId: null, results: {} }

function load(sessionId) {
  if (current.sessionId === sessionId) return current.results
  let results = {}
  try {
    results = JSON.parse(localStorage.getItem(KEY(sessionId))) || {}
  } catch { results = {} }
  current = { sessionId, results }
  return results
}

function persist() {
  if (!current.sessionId) return
  try {
    localStorage.setItem(KEY(current.sessionId), JSON.stringify(current.results))
  } catch { /* quota — the in-memory copy still serves this session */ }
}

const cellKey = (msgId, model) => `${msgId}::${model}`
const scoreKey = (msgId, model) => `ai::${msgId}::${model}`

export function getResult(sessionId, msgId, model) {
  return load(sessionId)[cellKey(msgId, model)] || null
}

export function setResult(sessionId, msgId, model, result) {
  const results = load(sessionId)
  results[cellKey(msgId, model)] = result
  persist()
  listeners.forEach(fn => { try { fn() } catch {} })
}

// AI scores live in the same store so the scoreboard can show the deterministic
// and the model-judged measures side by side — while keeping them in separate
// fields, never blended into one number.
export function getScore(sessionId, msgId, model) {
  return load(sessionId)[scoreKey(msgId, model)] || null
}

export function setScore(sessionId, msgId, model, score) {
  const results = load(sessionId)
  results[scoreKey(msgId, model)] = score
  persist()
  listeners.forEach(fn => { try { fn() } catch {} })
}

export function subscribe(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

// Summing rates across answers would weight a one-citation answer the same as
// a twelve-citation one, so the counts are pooled first and the rate derived
// from the totals.
export function aggregate(sessionId, modelKeys) {
  const results = load(sessionId)
  const totals = Object.fromEntries(modelKeys.map(k => [k, {
    answers: 0, checked: 0, verified: 0, fabricated: 0, mismatched: 0, unresolved: 0,
  }]))

  const aiScores = {}
  for (const [key, r] of Object.entries(results)) {
    if (key.startsWith('ai::')) {
      const model = key.split('::')[2]
      if (r && typeof r.overall === 'number') (aiScores[model] ||= []).push(r.overall)
      continue
    }
    const model = key.split('::')[1]
    const t = totals[model]
    if (!t || !r) continue
    t.answers += 1
    t.checked += r.checked || 0
    t.verified += r.verified || 0
    t.fabricated += r.fabricated || 0
    t.mismatched += r.mismatched || 0
    t.unresolved += r.unresolved || 0
  }

  return modelKeys.map(model => {
    const t = totals[model]
    // Only citations with a decided verdict count in the denominators — an
    // unreachable lookup is not evidence of anything, and folding it in either
    // direction would move a model's rate for a network reason.
    const decided = t.verified + t.fabricated
    const judged = t.verified
    const ai = aiScores[model] || []
    return {
      model,
      ...t,
      aiScore: ai.length ? ai.reduce((a, b) => a + b, 0) / ai.length : null,
      aiScored: ai.length,
      hallucinationRate: decided ? t.fabricated / decided : null,
      miscitationRate: judged ? t.mismatched / judged : null,
      // Share of cited sources that are real AND actually support the claim.
      accuracy: decided ? (t.verified - t.mismatched) / decided : null,
    }
  })
}

export function clearSession(sessionId) {
  try { localStorage.removeItem(KEY(sessionId)) } catch {}
  if (current.sessionId === sessionId) current = { sessionId, results: {} }
  listeners.forEach(fn => { try { fn() } catch {} })
}
