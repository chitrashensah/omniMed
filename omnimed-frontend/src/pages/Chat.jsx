import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { authedFetch, postJSON, askStream } from '../lib/api'
import * as chatStore from '../lib/chatStore'
import DOMPurify from 'dompurify'
import PromptSelector from '../components/PromptSelector.jsx'
import ApiKeyModal from '../components/ApiKeyModal.jsx'
import { BUILTIN_PROMPTS } from '../data/builtinPrompts.js'
import { buildUserKeys, modelHasKey } from '../lib/apiKeys.js'
import * as verifyStore from '../lib/verifyStore.js'
import './Chat.css'

// Keys are permanent identifiers — they are stored in `validations.model` and in
// cached conversations, so never rename one or point it at a different model.
// `gated` mirrors GATED_MODELS in the backend: metered at 5/day on the project's
// keys unless the user supplies their own.
const MODELS = [
  // Anthropic
  { key: 'opus55',  label: 'Claude Opus 5.5',    group: 'Anthropic', gated: true, color: '#6d28d9', bg: '#ede9fe' },
  { key: 'opus5',   label: 'Claude Opus 5',      group: 'Anthropic', gated: true, color: '#6d28d9', bg: '#ede9fe' },
  { key: 'fable51', label: 'Claude Fable 5.1',   group: 'Anthropic', gated: true, color: '#7e22ce', bg: '#f3e8ff' },
  { key: 'fable5',  label: 'Claude Fable 5',     group: 'Anthropic', gated: true, color: '#7e22ce', bg: '#f3e8ff' },
  { key: 'opus48',  label: 'Claude Opus 4.8',    group: 'Anthropic', gated: true, color: '#5b21b6', bg: '#ede9fe' },
  { key: 'sonnet55',label: 'Claude Sonnet 5.5',  group: 'Anthropic', gated: true, color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'sonnet5', label: 'Claude Sonnet 5',    group: 'Anthropic', gated: true, color: '#7c3aed', bg: '#f5f3ff' },
  { key: 'claude',  label: 'Claude Sonnet 4.6',  group: 'Anthropic', gated: true, color: '#8b5cf6', bg: '#f5f3ff' },
  // OpenAI
  { key: 'gpt6astra', label: 'GPT-6 Astra',      group: 'OpenAI', gated: true, color: '#15803d', bg: '#dcfce7' },
  { key: 'gpt61sol',  label: 'GPT-6.1 Sol',      group: 'OpenAI', gated: true, color: '#15803d', bg: '#dcfce7' },
  { key: 'gpt56sol',  label: 'GPT-5.6 Sol',      group: 'OpenAI', gated: true, color: '#166534', bg: '#dcfce7' },
  { key: 'gpt55',     label: 'GPT-5.5',          group: 'OpenAI', gated: true, color: '#166534', bg: '#dcfce7' },
  { key: 'gpt54mini', label: 'GPT-5.4 mini',     group: 'OpenAI', gated: true, color: '#047857', bg: '#d1fae5' },
  // DeepSeek — Pro only. deepseek-flash is still listed by their API but did
  // not answer a one-line prompt within the full 300s timeout on repeated
  // attempts, so offering it would just hang a box.
  { key: 'deepseek_pro',   label: 'DeepSeek V4 Pro',   group: 'DeepSeek', color: '#0369a1', bg: '#e0f2fe' },
  // Google
  { key: 'gemini38', label: 'Gemini 3.8 Flash',  group: 'Google', color: '#b45309', bg: '#fef3c7' },
  { key: 'gemini36', label: 'Gemini 3.6 Flash',  group: 'Google', color: '#b45309', bg: '#fef3c7' },
  { key: 'gemini35', label: 'Gemini 3.5 Flash',  group: 'Google', color: '#a16207', bg: '#fef9c3' },
  { key: 'gemini',   label: 'Gemini 2.5 Flash',  group: 'Google', color: '#ca8a04', bg: '#fef3c7' },
  // Qwen
  { key: 'qwen38flash', label: 'Qwen 3.8 Flash', group: 'Qwen', color: '#be185d', bg: '#fce7f3' },
  { key: 'qwen38max',   label: 'Qwen 3.8 Max',   group: 'Qwen', color: '#be185d', bg: '#fce7f3' },
  { key: 'qwen37plus',  label: 'Qwen 3.7 Plus',  group: 'Qwen', color: '#db2777', bg: '#fce7f3' },
  { key: 'qwen36flash', label: 'Qwen 3.6 Flash', group: 'Qwen', color: '#9d174d', bg: '#fce7f3' },
  { key: 'qwen',        label: 'Qwen 2.5 72B',   group: 'Qwen', color: '#a21caf', bg: '#fae8ff' },
  // Other providers
  { key: 'groq',    label: 'Groq GPT-OSS 120B',  group: 'Other', color: '#c2410c', bg: '#ffedd5' },
  { key: 'cohere',  label: 'Cohere Command A+',  group: 'Other', color: '#0f766e', bg: '#ccfbf1' },
]

const MODEL_GROUPS = ['Anthropic', 'OpenAI', 'DeepSeek', 'Google', 'Qwen', 'Other']

const MODES = [
  { key: 'normal',     label: 'Normal',     icon: '💬', desc: 'Plain chat — no system prompt' },
  { key: 'biomedical', label: 'Biomedical', icon: '🔬', desc: 'Expert biomedical researcher mode' },
]

function newSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11) + '_' + Date.now()
}

function convKey(userId)       { return `omnimed_conversations_${userId}` }
function msgKey(userId, id)    { return `omnimed_msgs_${userId}_${id}` }

// Which model sits in which box. Persisted so a reload keeps the workspace.
const BOX_KEY = 'omnimed_box_models'
const DEFAULT_BOXES = ['gemini38', 'deepseek_pro', 'groq', 'qwen38flash', 'cohere']
const MAX_BOXES = 5

function loadBoxModels() {
  try {
    const saved = JSON.parse(localStorage.getItem(BOX_KEY))
    const valid = Array.isArray(saved)
      ? saved.filter(k => MODELS.some(m => m.key === k))
      : []
    return valid.length ? valid.slice(0, MAX_BOXES) : DEFAULT_BOXES
  } catch { return DEFAULT_BOXES }
}

// Literature options offered per model. Presentational for now — selecting one
// records the choice but nothing is sent to the backend yet.
const DATABASES = [
  { key: 'pubmed',    label: 'PubMed' },
  { key: 'europepmc', label: 'Europe PMC' },
  { key: 'none',      label: 'No database' },
]

const DATE_RANGES = [
  { key: '1y',   label: 'Past year' },
  { key: '5y',   label: 'Past 5 years' },
  { key: '10y',  label: 'Past 10 years' },
  { key: 'all',  label: 'Any time' },
]

// Per-model options, keyed by model key.
const OPTS_KEY = 'omnimed_model_opts'

function loadModelOpts() {
  try {
    const saved = JSON.parse(localStorage.getItem(OPTS_KEY))
    return saved && typeof saved === 'object' ? saved : {}
  } catch { return {} }
}

// Column count for the fixed grid, including the conclusion box. Keeps boxes
// wide enough to read prose: 3 columns on a laptop is ~370px per box, which is
// about 50 characters a line. More columns than this stops being readable.
function gridCols(total) {
  if (total <= 2) return total
  if (total <= 4) return 2
  return 3
}

function saveConversation(userId, id, title, messages) {
  try {
    const all = JSON.parse(localStorage.getItem(convKey(userId)) || '[]')
    const idx = all.findIndex(c => c.id === id)
    const entry = { id, title, updatedAt: Date.now(), messageCount: messages.length }
    if (idx >= 0) all[idx] = entry
    else all.unshift(entry)
    localStorage.setItem(convKey(userId), JSON.stringify(all))
    localStorage.setItem(msgKey(userId, id), JSON.stringify(messages))
    window.dispatchEvent(new CustomEvent('omnimed:conversation_updated'))
  } catch {}
}

function loadMessages(userId, id) {
  try {
    return JSON.parse(localStorage.getItem(msgKey(userId, id)) || '[]')
  } catch { return [] }
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
}

// Markdown-lite: bold, code blocks, inline code, line breaks.
// SECURITY: escape the whole string FIRST, then apply formatting. Model output,
// web-search results, and text from uploaded files are untrusted — escaping up
// front means any literal HTML in them renders as text instead of executing.
function renderText(text) {
  if (!text) return ''
  return escHtml(text)
    .replace(/```([\s\S]*?)```/g, (_, code) => `<pre class="chat-code-block"><code>${code.replace(/^\w+\n/, '')}</code></pre>`)
    .replace(/`([^`]+)`/g, '<code class="chat-inline-code">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br/>')
}

// DOMPurify backstop: even if renderText changes, only this tiny tag/attr
// allowlist can ever reach the DOM. Used at every dangerouslySetInnerHTML site.
const SANITIZE_OPTS = { ALLOWED_TAGS: ['pre', 'code', 'strong', 'em', 'br'], ALLOWED_ATTR: ['class'] }
function safeHtml(text) {
  return DOMPurify.sanitize(renderText(text), SANITIZE_OPTS)
}

function exportCSV(messages, sessionId) {
  // One column per model rather than a fixed trio — the old hard-coded three
  // silently dropped every other model's answer from the export.
  const headers = ['Turn','Role','Mode','User Message', ...MODELS.map(m => m.label), 'Conclusion']
  const rows = [headers]
  let turn = 0
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    if (msg.role === 'user') {
      turn++
      const next = messages[i + 1]
      rows.push([
        turn,
        'User',
        msg.mode || '',
        msg.content || '',
        ...MODELS.map(m => next?.responses?.[m.key]?.text || ''),
        next?.conclusion || '',
      ])
    }
  }
  const csv = rows.map(r =>
    r.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')
  ).join('\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `omnimed-${sessionId || 'session'}-${new Date().toISOString().slice(0,10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function exportPrint(messages, sessionId) {
  const lines = messages.map(msg => {
    if (msg.role === 'user') {
      return `<div class="pe-user"><strong>You (${msg.mode}):</strong><p>${escHtml(msg.content || '')}</p></div>`
    }
    const panels = MODELS
      .filter(m => (msg.activeModels || ['claude','gpt4','gemini']).includes(m.key))
      .map(m => {
        const r = msg.responses?.[m.key]
        const text = r?.status === 'ok' ? escHtml(r.text) : `<em>${r?.error || 'no response'}</em>`
        return `<div class="pe-panel"><div class="pe-model">${m.label}</div><div class="pe-text">${text.replace(/\n/g,'<br/>')}</div></div>`
      }).join('')
    const conclusion = msg.conclusion
      ? `<div class="pe-conclusion"><strong>Conclusion:</strong><p>${escHtml(msg.conclusion).replace(/\n/g,'<br/>')}</p></div>`
      : ''
    return `<div class="pe-assistant"><div class="pe-panels">${panels}</div>${conclusion}</div>`
  }).join('')

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"/>
  <title>OmniMed Export — ${sessionId || 'session'}</title>
  <style>
    body{font-family:system-ui,sans-serif;font-size:13px;color:#111;max-width:960px;margin:0 auto;padding:24px}
    h1{font-size:1.1rem;margin-bottom:20px;color:#1a2b4a}
    .pe-user{background:#f0f4ff;border-left:3px solid #3b82f6;padding:10px 14px;margin:12px 0;border-radius:4px}
    .pe-assistant{margin:12px 0}
    .pe-panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
    .pe-panel{border:1px solid #ddd;border-radius:6px;padding:10px 12px}
    .pe-model{font-size:0.72rem;font-weight:700;text-transform:uppercase;color:#555;margin-bottom:6px}
    .pe-text{font-size:0.85rem;line-height:1.6}
    .pe-conclusion{background:#f0fdf4;border-left:3px solid #16a34a;padding:10px 14px;margin-top:8px;border-radius:4px;font-size:0.85rem}
    @media print{body{padding:0}}
  </style></head><body>
  <h1>OmniMed Research Session — ${new Date().toLocaleString()}</h1>
  ${lines}
  </body></html>`

  const w = window.open('', '_blank')
  w.document.write(html)
  w.document.close()
  w.focus()
  setTimeout(() => w.print(), 500)
}

const WL_SCORES = [1,2,3,4,5,6,7,8,9,10]
const WL_SCORE_COLOR = (score) => {
  if (score >= 8) return '#16a34a'
  if (score >= 5) return '#d97706'
  return '#dc2626'
}

const isUuid = (s) => typeof s === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)

// ── Wet-lab scoring ────────────────────────────────────────────────────────
// Attached to an individual response rather than to the box. A box now holds
// every turn for one model, so a single control in the header would be
// ambiguous about which answer it scores. The persisted key is unchanged —
// (session, msg, model) is already the natural key of the `validations` table.
function WetLabScore({ modelKey, msgId, sessionId, disabled }) {
  const { user } = useAuth()
  const storageKey = `omnimed_wlscore_${msgId}_${modelKey}`

  const [score, setScore]     = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey)) } catch { return null }
  })
  const [open, setOpen]       = useState(false)
  const [pending, setPending] = useState(null)
  const [notes, setNotes]     = useState('')
  const [saving, setSaving]   = useState(false)

  function openScorer() {
    setPending(score?.verdict || null)
    setNotes(score?.notes || '')
    setOpen(true)
  }

  async function saveScore() {
    if (!pending) return
    setSaving(true)
    const entry = { verdict: pending, notes: notes.trim(), savedAt: Date.now() }

    // Upsert on the natural key so re-scoring updates the same row instead of
    // inserting duplicates that would skew reliability averages.
    const { error } = await supabase.from('validations').upsert({
      session_id:          isUuid(sessionId) ? sessionId : null,
      user_id:             user?.id    || null,
      submitted_by:        user?.email || null,
      msg_id:              String(msgId),
      model:               modelKey,
      verdict:             String(pending),
      researcher_notes:    notes.trim() || null,
      updated_reliability: { [modelKey]: pending / 10 },
    }, { onConflict: 'session_id,msg_id,model' })

    if (error) console.error('[WetLab] Supabase upsert error:', error)

    localStorage.setItem(storageKey, JSON.stringify(entry))
    setScore(entry)
    setSaving(false)
    setOpen(false)
  }

  async function clearScore() {
    if (isUuid(sessionId)) {
      await supabase.from('validations')
        .delete()
        .match({ session_id: sessionId, msg_id: String(msgId), model: modelKey })
    }
    localStorage.removeItem(storageKey)
    setScore(null)
    setOpen(false)
  }

  const scoreNum   = score?.verdict ? Number(score.verdict) : null
  const scoreColor = scoreNum ? WL_SCORE_COLOR(scoreNum) : null

  return (
    <>
      <button
        className="wl-score-btn wl-score-btn--inline"
        style={scoreNum ? { color: scoreColor, borderColor: scoreColor, background: `${scoreColor}18` } : {}}
        onClick={openScorer}
        title="Submit Wet Lab Score"
        disabled={disabled}
      >
        {scoreNum ? `🔬 ${scoreNum}/10` : '🔬 Score'}
      </button>

      {open && (
        <div className="wl-scorer">
          <p className="wl-scorer-label">Rate this model (1 = poor, 10 = perfect)</p>
          <div className="wl-score-grid">
            {WL_SCORES.map(n => (
              <button
                key={n}
                className={`wl-score-num ${pending === n ? 'wl-score-num--active' : ''}`}
                style={pending === n ? { background: WL_SCORE_COLOR(n), color: '#fff', borderColor: WL_SCORE_COLOR(n) } : {}}
                onClick={() => setPending(n)}
              >
                {n}
              </button>
            ))}
          </div>
          {pending && (
            <p className="wl-score-hint" style={{ color: WL_SCORE_COLOR(pending) }}>
              {pending >= 8 ? 'Strong — prediction well supported by lab results' :
               pending >= 5 ? 'Moderate — partially correct or missing nuance' :
               'Weak — prediction poorly matched lab results'}
            </p>
          )}
          <textarea
            className="wl-notes"
            placeholder="Optional notes (e.g. which experiment validated this)…"
            value={notes}
            onChange={e => setNotes(e.target.value)}
            rows={2}
          />
          <div className="wl-scorer-actions">
            {score && <button className="wl-btn wl-btn--clear" onClick={clearScore}>Clear</button>}
            <button className="wl-btn wl-btn--cancel" onClick={() => setOpen(false)}>Cancel</button>
            <button className="wl-btn wl-btn--save" onClick={saveScore} disabled={!pending || saving}>
              {saving ? 'Saving…' : 'Save Score'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}

// ── Citation check ─────────────────────────────────────────────────────────
// On-demand verification of every PMID/DOI in an answer, against PubMed and
// Crossref. Two independent failure modes are reported separately because they
// mean different things:
//   fabricated  — the identifier does not exist
//   unsupported — the identifier is real but the paper is not about the claim.
// The second passes a naive existence check, so a model can look perfectly
// sourced while citing unrelated work. Observed in testing: an answer with
// three real PMIDs, none of them about the miRNA being discussed.
function CitationCheck({ text, disabled, sessionId, msgId, modelKey, auto = true }) {
  // A previous check for this exact answer is reused rather than re-run —
  // verification is deterministic, so a second call to PubMed would only
  // return the same answer more slowly.
  const cached = (sessionId && msgId && modelKey)
    ? verifyStore.getResult(sessionId, msgId, modelKey) : null

  const [state, setState] = useState(cached ? 'done' : 'idle')  // idle|loading|done|error
  const [result, setResult] = useState(cached)
  const [open, setOpen] = useState(false)
  const started = useRef(!!cached)

  const check = useCallback(async () => {
    if (started.current) return
    started.current = true
    setState('loading')
    try {
      const data = await postJSON('/literature/verify-citations', { text })
      if (data?.status === 'ok') {
        setResult(data)
        setState('done')
        if (sessionId && msgId && modelKey) {
          verifyStore.setResult(sessionId, msgId, modelKey, data)
        }
        return data
      }
      setState('error')
      started.current = false          // allow a retry
    } catch {
      setState('error')
      started.current = false
    }
    return null
  }, [text, sessionId, msgId, modelKey])

  // Checking costs no tokens — it is PubMed and Crossref, not a model — so it
  // runs on its own. Waiting for a click meant an answer's citations were only
  // ever checked if someone remembered to ask, which left the per-model
  // accuracy figures full of holes.
  useEffect(() => {
    if (auto && !disabled && !started.current && (text || '').trim()) check()
  }, [auto, disabled, text, check])

  async function run() {
    if (state === 'loading') return
    if (result) { setOpen(o => !o); return }    // already checked — just toggle
    const data = await check()
    if (data) setOpen(true)
  }

  const label = () => {
    if (state === 'loading') return 'Checking…'
    if (state === 'error') return '↻ Check failed'
    if (!result) return 'Check citations'
    if (result.checked === 0) return 'No citations'
    const bad = result.fabricated + result.mismatched + (result.retracted || 0)
    return bad === 0
      ? `✓ ${result.verified} cited, all real`
      : `⚠ ${bad} of ${result.checked} suspect`
  }

  // Colour reflects the worst finding: a fabricated id is worse than an
  // unsupported one, which is worse than a clean result.
  const tone = !result || result.checked === 0 ? ''
    : (result.fabricated > 0 || result.retracted > 0) ? 'cite-btn--bad'
    : result.mismatched > 0 ? 'cite-btn--warn'
    : 'cite-btn--ok'

  return (
    <>
      <button
        className={`mbox-act cite-btn ${tone}`}
        onClick={run}
        disabled={disabled || state === 'loading'}
        title="Verify every PMID, DOI and author-year citation in this answer against PubMed and Crossref"
      >
        {label()}
      </button>

      {open && result && (
        <div className="cite-panel">
          {result.checked === 0 ? (
            <p className="cite-none">
              This answer cites no sources — no PMIDs, DOIs, or author–year references to check.
            </p>
          ) : (
            <>
              {/* A plain-language verdict, so the panel can be read without
                  adding up chips to work out whether anything is wrong. */}
              <p className="cite-head">
                {result.fabricated + result.mismatched + (result.retracted || 0) === 0
                  ? `All ${result.checked} citation${result.checked !== 1 ? 's' : ''} check out.`
                  : `${result.fabricated + result.mismatched + (result.retracted || 0)} of ${result.checked} `
                    + `citation${result.checked !== 1 ? 's' : ''} ${
                      result.fabricated + result.mismatched + (result.retracted || 0) === 1 ? 'has' : 'have'} a problem.`}
              </p>
              <div className="cite-summary">
                <span className="cite-stat cite-stat--ok">{result.verified} real</span>
                {result.fabricated > 0 && (
                  <span className="cite-stat cite-stat--bad">{result.fabricated} fabricated</span>
                )}
                {result.retracted > 0 && (
                  <span className="cite-stat cite-stat--bad">{result.retracted} retracted</span>
                )}
                {result.mismatched > 0 && (
                  <span className="cite-stat cite-stat--warn">{result.mismatched} unsupported</span>
                )}
                {result.unresolved > 0 && (
                  <span className="cite-stat">{result.unresolved} unconfirmed</span>
                )}
              </div>

              <ul className="cite-list">
                {result.citations.map((c, i) => {
                  const sup = c.support?.support
                  const narrative = c.type === 'narrative'
                  const cls = c.exists === false ? 'bad'
                    : c.exists === null ? 'unknown'
                    : c.retracted ? 'bad'
                    : (sup === 'mismatched' || sup === 'unrelated') ? 'warn' : 'ok'
                  const status = c.exists === false ? 'Fabricated'
                    : c.exists === null
                      ? (c.error === 'UNCONFIRMED' ? 'Not found' : 'Lookup failed')
                    : c.retracted ? 'RETRACTED'
                    : sup === 'supported' ? 'Supported'
                    : (sup === 'mismatched' || sup === 'unrelated') ? 'Unsupported'
                    : 'Real'
                  const ref = narrative ? c.id : `${c.type.toUpperCase()} ${c.id}`
                  return (
                    <li key={`${c.id}-${i}`} className={`cite-item cite-item--${cls}`}>
                      <div className="cite-id">
                        {/* Verdict first: it is what the row is for. The
                            identifier follows as the means of checking it. */}
                        <span className={`cite-status cite-status--${cls}`}>{status}</span>
                        {c.url
                          ? <a href={c.url} target="_blank" rel="noopener noreferrer">{ref}</a>
                          : <span className="cite-ref">{ref}</span>}
                        {/* An author-year citation is matched by search, not
                            looked up by identifier. The marker keeps that
                            visible; the detail is on hover. */}
                        {narrative && (
                          <span className="cite-kind" title={
                            c.match
                              ? `Matched by search on ${c.match.replace(/\+/g, ' + ')} — `
                                + 'not an exact identifier lookup'
                              : 'Named in prose, with no PMID or DOI given'
                          }>≈ matched</span>
                        )}
                      </div>
                      {c.title && (
                        <div className="cite-title">
                          {narrative && <span className="cite-resolved">→ </span>}
                          {c.title}
                          {narrative && c.pmid && <span className="cite-resolved"> · PMID {c.pmid}</span>}
                        </div>
                      )}
                      {c.exists === null && c.reason && (
                        <div className="cite-reason">{c.reason}</div>
                      )}
                      {c.support?.reason && (sup === 'mismatched' || sup === 'unrelated') && (
                        <div className="cite-reason">{c.support.reason}</div>
                      )}
                    </li>
                  )
                })}
              </ul>
              <UnsourcedClaims text={text} />

              <p className="cite-foot">
                Verified against PubMed and Crossref. <b>Unsupported</b> = the paper is
                real but does not discuss this RNA. <b>Not found</b> = flagged for a
                human, not counted as fabricated.
              </p>
            </>
          )}
        </div>
      )}
    </>
  )
}

// Claims an answer made without citing anything.
//
// Verifying citations only measures the claims a model chose to cite, which
// quietly rewards citing less — an answer that asserts everything and cites
// nothing scores a perfect record. This asks the opposite question.
//
// Opt-in rather than automatic: it costs two PubMed calls per claim, and
// running it for every answer in every box would make the page crawl.
function UnsourcedClaims({ text }) {
  const [state, setState] = useState('idle')
  const [data, setData] = useState(null)

  async function run() {
    if (state === 'loading') return
    setState('loading')
    try {
      const r = await postJSON('/literature/unsourced-claims', { text })
      if (r?.status === 'ok') { setData(r); setState('done') }
      else setState('error')
    } catch { setState('error') }
  }

  if (state === 'idle') {
    return (
      <button className="council-more" onClick={run}
              title="Search PubMed for literature behind the claims this answer did not cite">
        Check uncited claims
      </button>
    )
  }
  if (state === 'loading') return <p className="cite-foot">Searching PubMed…</p>
  if (state === 'error') {
    return <button className="council-more" onClick={run}>↻ Check failed — retry</button>
  }
  if (!data?.checked) {
    return <p className="cite-foot">No uncited molecular claims found in this answer.</p>
  }

  return (
    <div className="unsourced">
      <div className="council-detail-h">
        Uncited claims — {data.none_found} of {data.checked} with nothing found
      </div>
      <ul className="cite-list">
        {data.claims.map((c, i) => (
          <li key={i} className={`cite-item cite-item--${
            c.status === 'none_found' ? 'warn' : c.status === 'related_found' ? 'ok' : 'unknown'}`}>
            <div className="cite-id">
              <span className={`cite-status cite-status--${
                c.status === 'none_found' ? 'warn' : c.status === 'related_found' ? 'ok' : 'unknown'}`}>
                {c.status === 'none_found' ? 'Nothing found'
                  : c.status === 'related_found' ? 'Related work' : 'Lookup failed'}
              </span>
            </div>
            <div className="cite-title">{c.claim}</div>
            {c.pmid && (
              <div className="cite-reason" style={{ color: 'var(--text-slate)' }}>
                <a href={c.url} target="_blank" rel="noopener noreferrer">PMID {c.pmid}</a>
                {' — '}{c.title}
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="cite-foot">
        A search cannot show a claim is true. <b>Related work</b> means literature on
        the same molecule and topic exists, not that it supports this claim.
        <b> Nothing found</b> is the informative result — worth checking by hand.
      </p>
    </div>
  )
}

// One scale for every score shown, so a bar, a number and a chip never
// disagree about whether a 6 is acceptable.
const band = (n) => n == null ? 'unknown' : n >= 7 ? 'ok' : n >= 4 ? 'warn' : 'bad'

// Per-model accuracy for the session: the deterministic citation record and the
// AI judge's score, side by side but never blended. One is measured from public
// registries; the other is a model's opinion. Averaging them would destroy the
// only comparison that matters.
function Scoreboard({ sessionId, modelKeys, onClose, turns }) {
  const { user } = useAuth()
  const [, force] = useState(0)
  const [scoring, setScoring] = useState('idle')
  useEffect(() => verifyStore.subscribe(() => force(n => n + 1)), [])

  const rows = verifyStore.aggregate(sessionId, modelKeys)
    .filter(r => r.answers > 0)
    .sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1))

  // One scoring pass over every model's answer to the latest question, rather
  // than a button per box. Scoring them together is what makes the numbers
  // comparable: same rubric, same question, and a judge that is never the model
  // being scored. Scored separately, a gap between two models would partly be
  // a gap between their judges.
  async function scoreAll() {
    const turn = turns?.[turns.length - 1]
    const a = turn?.assistant
    if (!a || scoring === 'loading') return

    const answers = {}
    for (const k of modelKeys) {
      const r = a.responses?.[k]
      if (r?.status !== 'error' && r?.text) answers[k] = r.text
    }
    if (!Object.keys(answers).length) return

    setScoring('loading')
    try {
      const verifications = {}
      for (const k of Object.keys(answers)) {
        const v = verifyStore.getResult(sessionId, a.id, k)
        if (v) verifications[k] = v
      }
      const data = await postJSON('/agent/score-all', {
        question: turn.user?.content || '', answers, verifications,
        user_keys: buildUserKeys(),
      })
      if (data?.status !== 'ok') { setScoring('error'); return }

      const dbRows = []
      for (const [k, sc] of Object.entries(data.scores || {})) {
        if (typeof sc?.overall !== 'number') continue
        verifyStore.setScore(sessionId, a.id, k, sc)

        // Both layers land in their own columns on one row, keyed the way the
        // wet-lab verdict is, so a later verdict joins them rather than making
        // a second row. That pairing is what makes "did the automated score
        // predict the wet-lab result?" answerable per answer.
        const v = verifications[k] || {}
        dbRows.push({
          session_id: sessionId,
          user_id: user?.id || null,
          submitted_by: user?.email || null,
          msg_id: String(a.id),
          model: k,
          db_checked: v.checked ?? null,
          db_verified: v.verified ?? null,
          db_fabricated: v.fabricated ?? null,
          db_mismatched: v.mismatched ?? null,
          db_hallucination_rate: v.hallucination_rate ?? null,
          db_miscitation_rate: v.miscitation_rate ?? null,
          db_detail: v.citations ? { citations: v.citations } : null,
          db_checked_at: v.checked != null ? new Date().toISOString() : null,
          ai_score: sc.overall,
          ai_dimensions: sc.scores || null,
          ai_rationale: sc.rationale || null,
          ai_red_flags: sc.red_flags || null,
          ai_judge_model: sc.judge || null,
          ai_saw_verification: !!sc.saw_verification,
          ai_scored_at: new Date().toISOString(),
        })
      }
      if (dbRows.length && isUuid(sessionId)) {
        const { error } = await supabase.from('validations')
          .upsert(dbRows, { onConflict: 'session_id,msg_id,model' })
        if (error) console.error('[Scoreboard] Supabase upsert error:', error)
      }
      setScoring('done')
    } catch { setScoring('error') }
  }

  const pct = (v) => v == null ? '—' : `${Math.round(v * 100)}%`

  return (
    <div className="scoreboard">
      <div className="scoreboard-head">
        <strong>Model accuracy this session</strong>
        <button className="council-more" style={{ marginLeft: 'auto', marginBottom: 0 }}
                onClick={scoreAll} disabled={scoring === 'loading'}>
          {scoring === 'loading' ? 'Scoring…'
            : scoring === 'error' ? '↻ Scoring failed'
            : 'Score latest answers'}
        </button>
        <button className="scoreboard-close" onClick={onClose} title="Close">✕</button>
      </div>

      {rows.length === 0 ? (
        <p className="cite-foot" style={{ marginTop: 0 }}>
          No answers checked yet. Citations are verified automatically as answers arrive.
        </p>
      ) : (
        <>
          <table className="scoreboard-table">
            <thead>
              <tr>
                <th>Model</th><th>Accurate</th>
                <th title="Cited identifiers that do not exist">Fabricated</th>
                <th title="Real papers that do not support the claim they were attached to">Unsupported</th>
                <th title="Average judge score, 1-10. A different model scores each answer, blind to who wrote it. An opinion, not a measurement.">AI score</th>
                <th title="Answers checked / citations found">n</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const m = MODELS.find(x => x.key === r.model)
                const a = r.accuracy
                return (
                  <tr key={r.model}>
                    <td className="sb-model">
                      <span className="sb-dot" style={{ background: m?.color || '#888' }} />
                      {m?.label || r.model}
                    </td>
                    <td>
                      <span className="sb-bar">
                        <span className={`sb-fill sb-fill--${band(a == null ? null : a * 10)}`}
                              style={{ width: `${Math.max(0, (a ?? 0) * 100)}%` }} />
                      </span>
                      <span className="sb-pct">{pct(a)}</span>
                    </td>
                    <td className={r.fabricated ? 'sb-bad' : ''}>{r.fabricated}</td>
                    <td className={r.mismatched ? 'sb-warn' : ''}>{r.mismatched}</td>
                    <td>
                      {r.aiScore == null
                        ? <span className="sb-n">—</span>
                        : <span className={`sb-ai sb-ai--${band(r.aiScore)}`}>
                            {r.aiScore.toFixed(1)}
                          </span>}
                    </td>
                    <td className="sb-n">{r.answers}&nbsp;/&nbsp;{r.checked}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="cite-foot">
            <b>Accurate</b> = cited sources that exist <i>and</i> discuss the claim, measured
            from PubMed and Crossref with no model involved. <b>AI score</b> is a separate
            model's opinion of the answer — useful, but not a measurement, and never
            averaged into the accuracy figure.
          </p>
        </>
      )}
    </div>
  )
}

function ErrorText({ error }) {
  const openKeys = () => window.dispatchEvent(new CustomEvent('omnimed:open_api_keys'))
  return (
    <p className="model-panel-error">
      {error === 'RATE_LIMIT'
        ? 'Rate limit hit — please wait a moment and retry.'
        : error === 'API_KEY_INVALID'
        ? 'API key invalid. Check your key in API Keys settings.'
        : error === 'TIMEOUT'
        ? 'Model timed out. Reasoning models can take several minutes on long research questions — try a narrower question, or a faster model.'
        : error === 'REFUSED'
        ? 'This model declined the request. Claude Fable 5 runs safety classifiers over research-biology topics and can decline legitimate biomedical questions — try Opus 5 or Sonnet 5 instead.'
        : error === 'TRUNCATED_BEFORE_ANSWER'
        ? 'The model used its whole token budget reasoning and produced no answer. Try a narrower question.'
        : error === 'EMPTY_RESPONSE'
        ? 'The model returned an empty response.'
        : error === 'INSUFFICIENT_CREDITS'
        ? <>Out of OpenRouter credits — this model could not be paid for. Top up at
           openrouter.ai/credits, or <button className="model-panel-link" onClick={openKeys}>add your own key</button>.</>
        : error === 'USAGE_LIMIT'
        ? 'This provider\'s API usage limit has been reached. Raise the spend cap in the provider console, or use a different model.'
        : error === 'SERVICE_UNAVAILABLE'
        ? 'Gemini is under high demand right now. Please retry in a moment.'
        : error === 'MODEL_UNAVAILABLE'
        ? <>This model is temporarily busy on the free tier. Retry, or <button className="model-panel-link" onClick={openKeys}>add your own key</button> for reliable access.</>
        : error === 'DAILY_LIMIT_REACHED'
        ? <>Daily free limit reached (5/day). <button className="model-panel-link" onClick={openKeys}>Add your API key</button> to continue.</>
        : `Error: ${error}`}
    </p>
  )
}

// The question is repeated at the top of every turn, in every box, so each box
// reads as a complete thread on its own.
function TurnQuestion({ user }) {
  return (
    <div className="mbox-q">
      {user.content || <em>(attachment only)</em>}
      {user.attachments?.length > 0 && (
        <span className="mbox-q-atts">
          {user.attachments.map((a, i) => (
            <span key={i} className="mbox-q-att">
              {a.file_type === 'image' ? '🖼️' : a.file_type === 'pdf' ? '📄' : '📎'} {a.filename}
            </span>
          ))}
        </span>
      )}
    </div>
  )
}

// One turn inside a model box: the question, then that model's answer.
function ModelTurn({ turn, modelKey, sessionId }) {
  const [copied, setCopied] = useState(false)
  const assistant = turn.assistant
  // A turn only queried the models that were in boxes at the time. If this
  // model wasn't one of them, say so rather than showing a false blank.
  const wasQueried = !assistant || (assistant.activeModels || []).includes(modelKey)
  const response   = assistant?.responses?.[modelKey]

  const isLoading = wasQueried && (!assistant || !response)
  const isStopped = response?.status === 'stopped'
  const isError   = response?.status === 'error' || response?.status === 'TIMED_OUT'
  const isOk      = !isLoading && !isStopped && !isError && !!response

  function copyText() {
    navigator.clipboard.writeText(response?.text || '').then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="mbox-turn" data-turn={turn.user.id}>
      <TurnQuestion user={turn.user} />

      {!wasQueried ? (
        <p className="mbox-notqueried">Not queried for this message.</p>
      ) : isLoading ? (
        <div className="model-panel-loading">
          <span className="dot-pulse" />
          <span className="dot-pulse" style={{ animationDelay: '0.2s' }} />
          <span className="dot-pulse" style={{ animationDelay: '0.4s' }} />
        </div>
      ) : isStopped ? (
        <p className="model-panel-error" style={{ color: '#f59e0b' }}>Response stopped by user.</p>
      ) : isError ? (
        <ErrorText error={response.error} />
      ) : (
        <div
          className="model-panel-text"
          dangerouslySetInnerHTML={{ __html: safeHtml(response.text || '') }}
        />
      )}

      {wasQueried && (isOk || isError || isStopped) && (
        <div className="mbox-actions">
          {isOk && (
            <button className="mbox-act" onClick={copyText} title="Copy response">
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          )}
          <WetLabScore
            modelKey={modelKey}
            msgId={assistant.id}
            sessionId={sessionId}
            disabled={!isOk}
          />
          {isOk && (
            <CitationCheck
              text={response.text || ''}
              sessionId={sessionId}
              msgId={assistant.id}
              modelKey={modelKey}
            />
          )}
          {isOk && response.served_model && (
            // The provider's own report of which model ran. Models are
            // unreliable about their own identity — one may answer "I'm Sonnet
            // 4.5" simply because it was trained before it existed — so this is
            // the only trustworthy provenance for a scored answer.
            <span
              className="mbox-served"
              title="Model the provider reported as serving this answer"
            >
              {response.served_model}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

// The council's verdict across every model's answer.
//
// Ordered by what a researcher needs first: the answer, then enough context to
// trust it, then the candidates, and only then the disagreements. The previous
// synthesis returned several paragraphs of prose, which meant reading the whole
// thing to find out what it concluded.
function CouncilVerdict({ verdict, judge }) {
  const [showDetail, setShowDetail] = useState(false)
  const extras = (verdict.disagreements?.length || 0) + (verdict.caveats?.length || 0)

  return (
    <div className="council">
      {verdict.headline && <p className="council-headline">{verdict.headline}</p>}
      {verdict.summary && <p className="council-summary">{verdict.summary}</p>}

      {verdict.candidates?.length > 0 && (
        <ul className="council-list">
          {verdict.candidates.map((c, i) => (
            <li key={`${c.name}-${i}`} className={`council-cand council-cand--${c.confidence}`}>
              <div className="council-cand-top">
                <span className="council-rank">{i + 1}</span>
                <span className="council-name">{c.name}</span>
                <span className={`council-conf council-conf--${c.confidence}`}>{c.confidence}</span>
                {c.models > 0 && (
                  <span className="council-models"
                        title="How many models proposed this. Agreement is weak evidence on its own — models share training data.">
                    {c.models} {c.models === 1 ? 'model' : 'models'}
                  </span>
                )}
              </div>
              {c.why && <div className="council-why">{c.why}</div>}
              {c.caveat && <div className="council-caveat">{c.caveat}</div>}
            </li>
          ))}
        </ul>
      )}

      {extras > 0 && (
        <>
          <button className="council-more" onClick={() => setShowDetail(v => !v)}>
            {showDetail ? 'Hide' : `Disagreements & caveats (${extras})`}
          </button>
          {showDetail && (
            <div className="council-detail">
              {verdict.disagreements?.length > 0 && (
                <>
                  <div className="council-detail-h">Where the models disagreed</div>
                  <ul className="council-bullets">
                    {verdict.disagreements.map((d, i) => <li key={i}>{d}</li>)}
                  </ul>
                </>
              )}
              {verdict.caveats?.length > 0 && (
                <>
                  <div className="council-detail-h">Caveats</div>
                  <ul className="council-bullets">
                    {verdict.caveats.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}
        </>
      )}

      <p className="cite-foot">
        Synthesised by {MODELS.find(m => m.key === judge)?.label || judge || 'a separate model'},
        which saw each answer's citation-check result but not which model wrote it.
      </p>
    </div>
  )
}

// A model box: one model, its whole conversation, its own scroll.
function ModelBox({ modelKey, onModelChange, takenModels, turns, sessionId,
                    maximized, onToggleMax, opts, onOptsChange, isMetered }) {
  const model = MODELS.find(m => m.key === modelKey) || MODELS[0]
  const o = opts || {}
  return (
    <div className="mbox">
      <div className="mbox-head" style={{ borderColor: model.color }}>
        <span className="mbox-head-hint">Model</span>
        <select
          className="mbox-select"
          // `backgroundColor`, not the `background` shorthand: the shorthand
          // resets background-image, which would wipe the chevron the
          // stylesheet draws and leave the control looking like a plain label.
          style={{ backgroundColor: model.bg, color: model.color }}
          value={model.key}
          onChange={e => onModelChange(e.target.value)}
          title="Change model for this box"
        >
          {MODEL_GROUPS.map(g => (
            <optgroup key={g} label={g}>
              {MODELS.filter(m => m.group === g).map(m => (
                <option
                  key={m.key}
                  value={m.key}
                  disabled={m.key !== model.key && takenModels.includes(m.key)}
                >
                  {m.label}{isMetered(m) ? ' · 5/day' : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button
          className="mbox-max"
          onClick={onToggleMax}
          title={maximized ? 'Restore grid' : 'Expand this box'}
        >
          {maximized ? '⤡' : '⤢'}
        </button>
      </div>

      {/* Per-model literature options. Placeholders — nothing is sent to the
          backend yet. Kept per box because one model may be given literature
          while another answers from training alone. */}
      <div className="mbox-tools">
        <select
          className={`mbox-tool ${o.db ? 'mbox-tool--on' : ''}`}
          value={o.db || ''}
          onChange={e => onOptsChange({ ...o, db: e.target.value })}
          title={`Literature source for ${model.label} (not wired up yet)`}
        >
          <option value="">📚 Select database</option>
          {DATABASES.map(d => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        <select
          className={`mbox-tool ${o.dates ? 'mbox-tool--on' : ''}`}
          value={o.dates || ''}
          onChange={e => onOptsChange({ ...o, dates: e.target.value })}
          title={`Publication date range for ${model.label} (not wired up yet)`}
        >
          <option value="">📅 Date range</option>
          {DATE_RANGES.map(d => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        {(o.db || o.dates) && <span className="mbox-optnote">not active yet</span>}
      </div>

      <div className="mbox-body">
        {turns.length === 0 ? (
          <p className="mbox-empty">Ask a question to see {model.label}'s answer here.</p>
        ) : (
          turns.map(t => (
            <ModelTurn key={t.user.id} turn={t} modelKey={modelKey} sessionId={sessionId} />
          ))
        )}
      </div>
    </div>
  )
}

// The conclusion box mirrors a model box, but its "answer" is the synthesis.
// A turn that was never synthesized before the next question records that
// plainly instead of leaving a silent gap.
function ConclusionBox({ turns, maximized, onToggleMax, onGenerate }) {
  const lastId = turns.length ? turns[turns.length - 1].user.id : null
  // An optional question aimed at the synthesis only. The individual models
  // have already answered and are never re-queried, so this can only change how
  // their answers are read, not what they said.
  const [focus, setFocus] = useState('')

  return (
    <div className="mbox mbox--conclusion">
      <div className="mbox-head mbox-head--conclusion">
        <span className="mbox-select mbox-select--static">🔍 Conclusion</span>
        <button
          className="mbox-max"
          onClick={onToggleMax}
          title={maximized ? 'Restore grid' : 'Expand this box'}
        >
          {maximized ? '⤡' : '⤢'}
        </button>
      </div>

      {/* Matches the height of the model boxes' options strip, so every box
          starts its content at the same eye level for comparison. */}
      <div className="mbox-tools">
        <span className="mbox-toolnote">Synthesized from every model's answer</span>
      </div>

      <div className="mbox-body">
        {turns.length === 0 ? (
          <p className="mbox-empty">The synthesis across all models appears here.</p>
        ) : turns.map(t => {
          const a       = t.assistant
          const active  = MODELS.filter(m => (a?.activeModels || []).includes(m.key))
          const allDone = !!a && active.length > 0 &&
            active.every(m => a.responses?.[m.key] && a.responses[m.key].status !== undefined)
          const isLast  = t.user.id === lastId

          return (
            <div className="mbox-turn" data-turn={t.user.id} key={t.user.id}>
              <TurnQuestion user={t.user} />

              {a?.conclusionLoading ? (
                <div className="model-panel-loading">
                  <span className="dot-pulse" />
                  <span className="dot-pulse" style={{ animationDelay: '0.2s' }} />
                  <span className="dot-pulse" style={{ animationDelay: '0.4s' }} />
                  <span className="mbox-synth">synthesizing…</span>
                </div>
              ) : a?.conclusion ? (
                <>
                  {a.conclusionFocus && (
                    <div className="concl-asked">
                      <span className="concl-asked-label">Asked</span> {a.conclusionFocus}
                    </div>
                  )}
                  {typeof a.conclusion === 'object' && a.conclusion !== null ? (
                    <CouncilVerdict verdict={a.conclusion} judge={a.conclusionJudge} />
                  ) : (
                    // Older turns, and the fallback when the judge ignored the
                    // schema, are still plain text.
                    <div
                      className="model-panel-text"
                      dangerouslySetInnerHTML={{ __html: safeHtml(String(a.conclusion)) }}
                    />
                  )}
                </>
              ) : isLast ? (
                <div className="concl-ask">
                  <textarea
                    className="concl-ask-input"
                    placeholder="Optional — ask the synthesis something (e.g. “rank these by strength of evidence”). Only the conclusion sees this; the models are not re-queried."
                    value={focus}
                    onChange={e => setFocus(e.target.value)}
                    rows={2}
                    disabled={!allDone}
                  />
                  <button
                    className="btn-conclusion"
                    onClick={() => onGenerate(t, focus)}
                    disabled={!allDone}
                    title={allDone ? 'Synthesize across all models' : 'Waiting for all models to finish'}
                  >
                    🔍 Get Conclusion
                  </button>
                </div>
              ) : (
                <p className="mbox-notqueried">No conclusion returned.</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function Chat() {
  const { sessionId: urlSessionId } = useParams()
  const navigate = useNavigate()
  const { user, hasBackendAccess } = useAuth()
  const userId = user?.id || 'anonymous'

  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('normal')
  const [selectedPromptId, setSelectedPromptId] = useState(null)
  const [userPrompts, setUserPrompts] = useState([])
  const [apiKeyModal, setApiKeyModal] = useState(null) // null | model key string
  const [sending, setSending] = useState(false)
  const [attachments, setAttachments] = useState([])
  const [boxModels, setBoxModels] = useState(loadBoxModels)
  const [maximized, setMaximized] = useState(null)   // null | model key | 'conclusion'
  const [activeTurn, setActiveTurn] = useState(null)
  const [showScoreboard, setShowScoreboard] = useState(false)
  // Web search is off by default and costs real money when on: the tool
  // definition alone adds ~2,200 input tokens to every message, and each search
  // the model runs is billed on top.
  const [webSearch, setWebSearch] = useState(false)
  // Per-model retrieval options, keyed by model rather than by box position so
  // the setting follows a model when it is moved to a different box.
  // Placeholders — not yet sent to the backend.
  const [modelOpts, setModelOpts] = useState(loadModelOpts)
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)
  const abortControllerRef = useRef(null)

  // The query fans out to whichever models currently occupy boxes.
  const selectedModels = boxModels

  useEffect(() => {
    try { localStorage.setItem(BOX_KEY, JSON.stringify(boxModels)) } catch {}
  }, [boxModels])

  useEffect(() => {
    try { localStorage.setItem(OPTS_KEY, JSON.stringify(modelOpts)) } catch {}
  }, [modelOpts])

  const setModelOpt = useCallback((key, next) => {
    setModelOpts(prev => ({ ...prev, [key]: next }))
  }, [])

  // Pair each user message with its assistant reply. Every box walks this same
  // list, so turn N means the same thing in all of them.
  const turns = useMemo(() => {
    const out = []
    for (let i = 0; i < messages.length; i++) {
      if (messages[i].role !== 'user') continue
      const next = messages[i + 1]
      out.push({ user: messages[i], assistant: next?.role === 'assistant' ? next : null })
    }
    return out
  }, [messages])

  // A gated model is only actually metered for *this* user. Admins and granted
  // users have unlimited backend access, and anyone who supplied their own
  // provider key is billed on that key — none of them should see a 5/day mark.
  const isMetered = useCallback(
    (m) => !!m?.gated && !hasBackendAccess && !modelHasKey(m.key),
    [hasBackendAccess],
  )

  const setBoxModel = useCallback((idx, key) => {
    setBoxModels(prev => prev.map((k, i) => (i === idx ? key : k)))
  }, [])

  const addBox = useCallback(() => {
    setBoxModels(prev => {
      if (prev.length >= MAX_BOXES) return prev
      const free = MODELS.find(m => !prev.includes(m.key))
      return free ? [...prev, free.key] : prev
    })
  }, [])

  const removeBox = useCallback(() => {
    setBoxModels(prev => (prev.length > 1 ? prev.slice(0, -1) : prev))
  }, [])

  // Align every box on the same turn. Response lengths differ wildly between
  // models, so turn N sits at a different pixel offset in each box — matching
  // scrollTop would misalign. Anchoring on the turn element is what lines the
  // answers up.
  //
  // Offsets are measured from the scroll container's own box rather than with
  // offsetTop: offsetTop is relative to the nearest *positioned* ancestor,
  // which here is the document, so it silently included the page header and
  // each box's position in the grid and scrolled to the wrong place.
  const alignToTurn = useCallback((turnId, behavior = 'smooth') => {
    if (turnId == null) return
    const sel = typeof CSS !== 'undefined' && CSS.escape
      ? `[data-turn="${CSS.escape(String(turnId))}"]`
      : `[data-turn="${turnId}"]`
    for (const el of document.querySelectorAll('.mbox-body')) {
      const target = el.querySelector(sel)
      if (!target) continue
      const delta = target.getBoundingClientRect().top - el.getBoundingClientRect().top
      el.scrollTo({ top: el.scrollTop + delta - 10, behavior })
    }
    setActiveTurn(turnId)
  }, [])

  // Load or create session — cached instantly, then refreshed from Supabase
  // (source of truth) so history follows the user across devices.
  useEffect(() => {
    if (!urlSessionId) {
      setSessionId(null)
      setMessages([])
      return
    }
    setSessionId(urlSessionId)
    setMessages(loadMessages(userId, urlSessionId))   // instant: local cache

    let cancelled = false
    chatStore.loadMessages(urlSessionId)
      .then(remote => {
        if (cancelled || !remote || remote.length === 0) return
        setMessages(remote)
        try { localStorage.setItem(msgKey(userId, urlSessionId), JSON.stringify(remote)) } catch { /* quota */ }
      })
      .catch(() => { /* offline — keep cached */ })
    return () => { cancelled = true }
  }, [urlSessionId, userId])

  // New chat event from sidebar
  useEffect(() => {
    const handler = () => {
      setSessionId(null)
      setMessages([])
      setInput('')
      setAttachments([])
      setMaximized(null)
      lastTurnCount.current = 0
    }
    window.addEventListener('omnimed:new_chat', handler)
    return () => window.removeEventListener('omnimed:new_chat', handler)
  }, [])

  // Jump every box to the newest turn when one is added. Keyed on the turn
  // count rather than on `messages`: the previous version fired on every
  // streamed chunk, which is why the view fought you while answers arrived.
  const lastTurnCount = useRef(0)
  useEffect(() => {
    if (turns.length && turns.length !== lastTurnCount.current) {
      lastTurnCount.current = turns.length
      const id = turns[turns.length - 1].user.id
      // Wait for the new turn to be in the DOM before measuring against it.
      requestAnimationFrame(() => requestAnimationFrame(() => alignToTurn(id, 'auto')))
    }
  }, [turns, alignToTurn])

  // Create the session row first and adopt its DB uuid as the canonical id, so
  // documents, usage logs, validations and atomic turn-append all link to it.
  // Falls back to a local id only if the DB write fails (offline / RLS).
  // Open API keys modal from sidebar button
  useEffect(() => {
    const handler = () => setApiKeyModal('openrouter')
    window.addEventListener('omnimed:open_api_keys', handler)
    return () => window.removeEventListener('omnimed:open_api_keys', handler)
  }, [])

  // Load user's custom prompts for resolving selected prompt content
  useEffect(() => {
    if (!user) return
    supabase.from('user_prompts').select('*').then(({ data }) => setUserPrompts(data || []))
  }, [user])

  function resolvePromptContent() {
    if (!selectedPromptId) return null
    const builtin = BUILTIN_PROMPTS.find(p => p.id === selectedPromptId)
    if (builtin) return builtin.content
    const custom = userPrompts.find(p => p.id === selectedPromptId)
    return custom?.content || null
  }

  async function getOrCreateSession() {
    if (sessionId) return sessionId
    let id = null
    if (user?.id) {
      try {
        const { data, error } = await supabase
          .from('sessions')
          .insert({ user_id: user.id })
          .select('session_id')
          .single()
        if (!error && data?.session_id) id = data.session_id
      } catch { /* fall through to local id */ }
    }
    if (!id) id = newSessionId()   // local-only fallback
    setSessionId(id)
    navigate(`/chat/${id}`, { replace: true })
    return id
  }

  function stopSending() {
    abortControllerRef.current?.abort()
    setSending(false)
    // Keep everything in the chat — just mark any still-pending models as stopped
    // so history stays balanced (every user turn has a paired assistant turn)
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (!last || last.role !== 'assistant') return prev
      const updatedResponses = { ...last.responses }
      for (const key of Object.keys(updatedResponses)) {
        if (!updatedResponses[key] || updatedResponses[key] === null) {
          updatedResponses[key] = { model: key, status: 'stopped', text: '[Response stopped]' }
        } else if (updatedResponses[key].status !== 'ok') {
          updatedResponses[key] = { model: key, status: 'stopped', text: '[Response stopped]' }
        }
      }
      return prev.map(m => m.id === last.id ? { ...m, responses: updatedResponses } : m)
    })
  }

  async function send() {
    const text = input.trim()
    const readyAtts = attachments.filter(a => !a.uploading && !a.error)
    if ((!text && readyAtts.length === 0) || sending) return

    abortControllerRef.current = new AbortController()
    const signal = abortControllerRef.current.signal

    setSending(true)
    setInput('')
    setAttachments([])

    const sid = await getOrCreateSession()
    const msgId = Date.now()

    const userMsg = { id: msgId, role: 'user', content: text, mode, attachments: readyAtts.map(a => ({ filename: a.filename, file_type: a.file_type })) }
    const assistantMsg = {
      id: msgId + 1,
      role: 'assistant',
      mode,
      activeModels: [...selectedModels],
      // Seed from the models actually in boxes — the old hard-coded trio left
      // stale keys behind and no key for anything else.
      responses: Object.fromEntries(selectedModels.map(k => [k, null])),
      conclusion: null,
      conclusionLoading: false,
    }

    const newMessages = [...messages, userMsg, assistantMsg]
    setMessages(newMessages)
    saveConversation(userId, sid, text.slice(0, 50), newMessages)

    // Per-model history: this chat, same mode, and only the turns this model
    // answered. A question is sent with its own answer or not at all — a model
    // added to a box later, or one that failed, used to receive a run of
    // questions with no replies, which cost tokens and confused it.
    // 12 items (6 exchanges) is what the backend keeps (MAX_HISTORY_ITEMS).
    function buildHistory(modelKey, existingMessages, currentMode) {
      const history = []
      let question = null
      for (const m of existingMessages) {
        if (m.mode !== currentMode) continue
        if (m.role === 'user') {
          question = m.content || '[Shared file(s)]'
        } else if (m.role === 'assistant' && question != null) {
          const r = m.responses?.[modelKey]
          const answer = r?.status === 'ok' ? r.text
                       : r?.status === 'stopped' ? '[Response was stopped by user]'
                       : null
          if (answer) history.push({ role: 'user', content: question }, { role: 'assistant', content: answer })
          question = null
        }
      }
      return history.slice(-12)
    }

    // One backend call fans out to all selected models; results stream back
    // over SSE and we render each panel the moment its model finishes.
    const histories = {}
    for (const key of selectedModels) histories[key] = buildHistory(key, messages, mode)

    const received = new Set()
    const finalResponses = {}
    // Stable conversation title = the first user message of the thread.
    const convTitle = ((messages.find(m => m.role === 'user')?.content || text) || 'Untitled').slice(0, 50)

    // Apply a per-model response into state + cache
    const applyResponse = (modelKey, response) => {
      received.add(modelKey)
      finalResponses[modelKey] = response
      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === assistantMsg.id
            ? { ...m, responses: { ...m.responses, [modelKey]: response } }
            : m
        )
        saveConversation(userId, sid, convTitle, updated)
        return updated
      })
    }

    // Mark any models that never sent a result as errored
    const markRemaining = (errText) => {
      for (const key of selectedModels) {
        if (!received.has(key)) finalResponses[key] = { model: key, status: 'error', error: errText }
      }
      setMessages(prev => prev.map(m => {
        if (m.id !== assistantMsg.id) return m
        const resp = { ...m.responses }
        for (const key of selectedModels) {
          if (!received.has(key)) resp[key] = { model: key, status: 'error', error: errText }
        }
        return { ...m, responses: resp }
      }))
    }

    try {
      await askStream(
        { message: text, models: selectedModels, mode, session_id: sid, histories, attachments: readyAtts,
          system_prompt: resolvePromptContent(), user_keys: buildUserKeys(), web_search: webSearch },
        {
          signal,
          onModel: ({ model, response }) => applyResponse(model, response),
          onError: (e) => markRemaining(
            e?.status === 401 ? 'Session expired — please sign in again.' : 'Request failed.'
          ),
        }
      )
    } catch (err) {
      if (err.name !== 'AbortError') markRemaining('Network error')
    }

    // Persist the completed turn server-side (source of truth). No-ops for
    // local-only session ids; localStorage already holds the cached copy.
    const finalAssistant = { ...assistantMsg, responses: { ...assistantMsg.responses, ...finalResponses } }
    chatStore.saveTurn(sid, userId, convTitle, [userMsg, finalAssistant]).catch(() => {})

    setSending(false)
  }

  async function getConclusion(assistantMsgId, responses, msgMode, activeModels, focus = '') {
    setMessages(prev => prev.map(m =>
      m.id === assistantMsgId ? { ...m, conclusionLoading: true } : m
    ))

    // Only include models that actually responded
    const modelResponses = {}
    const modelLabels = {}
    MODELS.filter(m => (activeModels || []).includes(m.key)).forEach(m => {
      const text = responses[m.key]?.text || responses[m.key]?.raw_response
      if (text) {
        modelResponses[m.key] = text
        modelLabels[m.key] = m.label
      }
    })

    // The citation checks already ran automatically for this turn, so the
    // council is told which answers held up rather than having to take every
    // claim at face value. Agreement between models is weak evidence on its
    // own — they share training data — so verification is what breaks ties.
    const verifications = {}
    for (const key of Object.keys(modelResponses)) {
      const v = verifyStore.getResult(sessionId, assistantMsgId, key)
      if (v) verifications[key] = {
        checked: v.checked, verified: v.verified,
        fabricated: v.fabricated, mismatched: v.mismatched,
      }
    }

    const question = messages.find(m => m.id === assistantMsgId)?.question
      || [...messages].reverse().find(m => m.role === 'user')?.content || ''

    try {
      const sid = sessionId || newSessionId()
      const data = await postJSON('/agent/council', {
        session_id: sid, question, answers: modelResponses, verifications,
        focus: focus || undefined, user_keys: buildUserKeys(),
      })

      // `verdict` is the structured result the box renders. `raw` is the
      // fallback when the judge ignored the schema — a readable paragraph
      // still beats an error message.
      const conclusion = data.verdict || data.raw || 'No conclusion returned.'

      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, conclusion, conclusionLoading: false,
                conclusionJudge: data.judge || null,
                conclusionFocus: focus?.trim() || null }
            : m
        )
        if (sessionId) saveConversation(userId, sessionId, '', updated)
        return updated
      })

      // Persist the conclusion onto the assistant turn (title unchanged).
      const assistantWithConclusion = {
        id: assistantMsgId, role: 'assistant', mode: msgMode,
        activeModels, responses, conclusion,
        conclusionJudge: data.judge || null,
        conclusionFocus: focus?.trim() || null,
      }
      chatStore.saveTurn(sessionId, userId, null, [assistantWithConclusion]).catch(() => {})
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === assistantMsgId
          ? { ...m, conclusion: 'Failed to get conclusion — check backend.', conclusionLoading: false }
          : m
      ))
    }
  }

  async function handleFileSelect(e) {
    const files = Array.from(e.target.files)
    e.target.value = ''
    for (const file of files) {
      const attId = `att_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
      setAttachments(prev => [...prev, { id: attId, filename: file.name, file_type: 'uploading', uploading: true }])
      try {
        const fd = new FormData()
        fd.append('file', file)
        const res = await authedFetch('/upload', { method: 'POST', body: fd })
        const data = await res.json()
        if (!res.ok || data.error) {
          setAttachments(prev => prev.map(a => a.id === attId ? { ...a, uploading: false, error: data.error || 'Upload failed' } : a))
        } else {
          const warning = data.warning === 'SCANNED_PDF' ? 'scanned PDF — no text' : (data.truncated ? 'truncated' : null)
          setAttachments(prev => prev.map(a => a.id === attId ? { ...data, id: attId, uploading: false, warning } : a))
        }
      } catch {
        setAttachments(prev => prev.map(a => a.id === attId ? { ...a, uploading: false, error: 'Network error' } : a))
      }
    }
  }

  function removeAttachment(id) {
    setAttachments(prev => prev.filter(a => a.id !== id))
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="chat-page">
      {/* Mode bar */}
      <div className="chat-modebar">
        {MODES.map(m => (
          <button
            key={m.key}
            className={`chat-mode-btn ${mode === m.key ? 'chat-mode-btn--active' : ''}`}
            data-mode={m.key}
            onClick={() => setMode(m.key)}
            title={m.desc}
          >
            <span>{m.icon}</span>
            <span>{m.label}</span>
          </button>
        ))}

        {messages.length > 0 && (
          <div className="chat-export-btns">
            <button
              className="chat-export-btn"
              onClick={() => exportCSV(messages, sessionId)}
              title="Export as CSV"
            >⬇ CSV</button>
            <button
              className="chat-export-btn"
              onClick={() => exportPrint(messages, sessionId)}
              title="Export as PDF"
            >🖨 PDF</button>
          </div>
        )}
      </div>

      {/* Turn navigator — aligns every box on the same question */}
      {turns.length > 0 && !maximized && (
        <div className="chat-turnbar">
          {turns.length > 1 && (
            <>
              <span className="chat-turnbar-label">Jump to question</span>
              <div className="chat-turnpills">
                {turns.map((t, i) => (
                  <button
                    key={t.user.id}
                    className={`chat-turnpill ${activeTurn === t.user.id ? 'chat-turnpill--active' : ''}`}
                    onClick={() => alignToTurn(t.user.id)}
                    title={t.user.content?.slice(0, 80) || `Question ${i + 1}`}
                  >
                    {i + 1}
                  </button>
                ))}
              </div>
            </>
          )}
          <button
            className={`chat-scoreboard-btn ${showScoreboard ? 'chat-scoreboard-btn--on' : ''}`}
            onClick={() => setShowScoreboard(v => !v)}
            title="Per-model citation accuracy, pooled across this session"
          >
            Accuracy
          </button>
        </div>
      )}

      {showScoreboard && !maximized && (
        <Scoreboard
          sessionId={sessionId}
          modelKeys={boxModels}
          turns={turns}
          onClose={() => setShowScoreboard(false)}
        />
      )}

      {/* Fixed grid — the page itself never scrolls, each box scrolls alone */}
      <div
        className="chat-grid"
        style={{ '--cols': maximized ? 1 : gridCols(boxModels.length + 1) }}
      >
        {boxModels.map((key, idx) => (
          (!maximized || maximized === key) && (
            <ModelBox
              key={`${key}-${idx}`}
              modelKey={key}
              onModelChange={k => setBoxModel(idx, k)}
              takenModels={boxModels}
              turns={turns}
              sessionId={sessionId}
              opts={modelOpts[key]}
              onOptsChange={next => setModelOpt(key, next)}
              isMetered={isMetered}
              maximized={maximized === key}
              onToggleMax={() => setMaximized(maximized === key ? null : key)}
            />
          )
        ))}

        {(!maximized || maximized === 'conclusion') && (
          <ConclusionBox
            turns={turns}
            maximized={maximized === 'conclusion'}
            onToggleMax={() => setMaximized(maximized === 'conclusion' ? null : 'conclusion')}
            onGenerate={(t, focus) => t.assistant && getConclusion(
              t.assistant.id, t.assistant.responses, t.assistant.mode, t.assistant.activeModels, focus
            )}
          />
        )}
      </div>

      {/* Input bar */}
      <div className="chat-inputbar">
        {/* Attachment chips */}
        {attachments.length > 0 && (
          <div className="chat-attachments">
            {attachments.map(att => (
              <div key={att.id} className={`att-chip ${att.error ? 'att-chip--error' : ''} ${att.uploading ? 'att-chip--loading' : ''}`}>
                <span className="att-chip-icon">{att.file_type === 'image' ? '🖼️' : att.file_type === 'pdf' ? '📄' : att.file_type === 'docx' || att.file_type === 'doc' ? '📝' : att.uploading ? '⏳' : '📎'}</span>
                <span className="att-chip-name">{att.filename}</span>
                {att.warning && <span className="att-chip-tag">{att.warning}</span>}
                {att.error && <span className="att-chip-tag att-chip-tag--error">{att.error}</span>}
                {!att.uploading && (
                  <button className="att-chip-remove" onClick={() => removeAttachment(att.id)}>×</button>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="chat-input-wrap">
          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.doc,.docx,.csv,.txt,.md,.jpg,.jpeg,.png,.gif,.webp"
            style={{ display: 'none' }}
            onChange={handleFileSelect}
          />
          <button
            className="chat-attach-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={sending}
            title="Attach file (PDF, Word, CSV, Image)"
          >📎</button>

          <PromptSelector
            mode={mode}
            selectedPromptId={selectedPromptId}
            onSelect={setSelectedPromptId}
          />

          <button
            className={`query-opt ${webSearch ? 'query-opt--on' : ''}`}
            onClick={() => setWebSearch(v => !v)}
            title={webSearch
              ? 'Web search ON — models may search the live web. Costs more per message.'
              : 'Web search OFF — models answer from training data only. Much cheaper.'}
            disabled={sending}
          >
            🌐 Web search{webSearch ? ' · on' : ''}
          </button>


          {/* Box count. Which model goes in which box is chosen in its header. */}
          <div className="box-count">
            <button
              className="box-count-btn"
              onClick={removeBox}
              disabled={boxModels.length <= 1 || sending}
              title="Remove a model box"
            >−</button>
            <span className="box-count-label">
              {boxModels.length} model{boxModels.length !== 1 ? 's' : ''}
              {boxModels.some(k => isMetered(MODELS.find(m => m.key === k))) && (
                <span className="model-toggle-badge" title="5 free/day, then add your key">5/day</span>
              )}
            </span>
            <button
              className="box-count-btn"
              onClick={addBox}
              disabled={boxModels.length >= MAX_BOXES || sending}
              title="Add a model box"
            >+</button>
          </div>
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            placeholder={
              mode === 'biomedical'
                ? 'Ask a biomedical research question…'
                : `Ask anything across ${selectedModels.length} selected model${selectedModels.length !== 1 ? 's' : ''}…`
            }
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={sending}
          />
          {sending ? (
            <button className="chat-stop-btn" onClick={stopSending} title="Stop">
              ■
            </button>
          ) : (
            <button
              className="chat-send-btn"
              onClick={send}
              disabled={!input.trim() && attachments.filter(a => !a.uploading && !a.error).length === 0}
            >
              ↑
            </button>
          )}
        </div>
      </div>

      {apiKeyModal && (
        <ApiKeyModal
          initialTab={apiKeyModal === 'claude' ? 'claude' : apiKeyModal === 'gpt4' ? 'gpt4' : 'openrouter'}
          onClose={() => setApiKeyModal(null)}
        />
      )}
    </div>
  )
}
