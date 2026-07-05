import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { supabase } from '../lib/supabase'
import { authedFetch, postJSON, askStream } from '../lib/api'
import * as chatStore from '../lib/chatStore'
import DOMPurify from 'dompurify'
import Logo from '../components/Logo.jsx'
import PromptSelector from '../components/PromptSelector.jsx'
import ApiKeyModal from '../components/ApiKeyModal.jsx'
import { BUILTIN_PROMPTS } from '../data/builtinPrompts.js'
import { buildUserKeys, modelHasKey } from '../lib/apiKeys.js'
import './Chat.css'

const MODELS = [
  { key: 'gemini',   label: 'Gemini 2.5 Flash',   color: '#b45309', bg: '#fef3c7' },
  { key: 'deepseek', label: 'DeepSeek V3',         color: '#0369a1', bg: '#e0f2fe' },
  { key: 'groq',     label: 'Groq Llama 3.3',      color: '#7c3aed', bg: '#f3e8ff' },
  { key: 'qwen',     label: 'Qwen 2.5 72B',        color: '#be185d', bg: '#fce7f3' },
  { key: 'cohere',   label: 'Cohere Command R+',   color: '#0f766e', bg: '#ccfbf1' },
  { key: 'claude',   label: 'Claude Sonnet 4.6',   color: '#6d28d9', bg: '#ede9fe' },
  { key: 'gpt4',     label: 'GPT-4o',              color: '#15803d', bg: '#dcfce7' },
]

const MODES = [
  { key: 'normal',     label: 'Normal',     icon: '💬', desc: 'Plain chat — no system prompt' },
  { key: 'biomedical', label: 'Biomedical', icon: '🔬', desc: 'Expert biomedical researcher mode' },
]

function newSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11) + '_' + Date.now()
}

function convKey(userId)       { return `omnimed_conversations_${userId}` }
function msgKey(userId, id)    { return `omnimed_msgs_${userId}_${id}` }

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
  const headers = ['Turn','Role','Mode','User Message','Claude Sonnet 4.6','GPT-4o','Gemini 2.5 Flash','Conclusion']
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
        next?.responses?.claude?.text  || '',
        next?.responses?.gpt4?.text    || '',
        next?.responses?.gemini?.text  || '',
        next?.conclusion               || '',
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

function ModelPanel({ model, response, loading, msgId, sessionId }) {
  const isLoading  = loading || !response
  const isStopped  = response?.status === 'stopped'
  const isError    = response?.status === 'error' || response?.status === 'TIMED_OUT'
  const [copied, setCopied] = useState(false)
  const { user } = useAuth()

  const storageKey = `omnimed_wlscore_${msgId}_${model.key}`
  const [wlScore, setWlScore]     = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey)) } catch { return null }
  })
  const [wlOpen, setWlOpen]       = useState(false)
  const [wlPending, setWlPending] = useState(null)
  const [wlNotes, setWlNotes]     = useState('')
  const [wlSaving, setWlSaving]   = useState(false)

  function openScorer() {
    setWlPending(wlScore?.verdict || null)
    setWlNotes(wlScore?.notes || '')
    setWlOpen(true)
  }

  async function saveScore() {
    if (!wlPending) return
    setWlSaving(true)
    const entry = { verdict: wlPending, notes: wlNotes.trim(), savedAt: Date.now() }

    // Upsert on the natural key (session, message, model) so re-scoring updates
    // the same row instead of inserting duplicates that skew reliability averages.
    const { error } = await supabase.from('validations').upsert({
      session_id:          isUuid(sessionId) ? sessionId : null,
      user_id:             user?.id    || null,
      submitted_by:        user?.email || null,
      msg_id:              String(msgId),
      model:               model.key,
      verdict:             String(wlPending),
      researcher_notes:    wlNotes.trim() || null,
      updated_reliability: { [model.key]: wlPending / 10 },
    }, { onConflict: 'session_id,msg_id,model' })

    if (error) console.error('[WetLab] Supabase upsert error:', error)

    localStorage.setItem(storageKey, JSON.stringify(entry))
    setWlScore(entry)
    setWlSaving(false)
    setWlOpen(false)
  }

  async function clearScore() {
    // Remove the persisted row too, so a cleared score doesn't linger in the data.
    if (isUuid(sessionId)) {
      await supabase.from('validations')
        .delete()
        .match({ session_id: sessionId, msg_id: String(msgId), model: model.key })
    }
    localStorage.removeItem(storageKey)
    setWlScore(null)
    setWlOpen(false)
  }

  const scoreNum   = wlScore?.verdict ? Number(wlScore.verdict) : null
  const scoreColor = scoreNum ? WL_SCORE_COLOR(scoreNum) : null

  function copyText() {
    const text = response?.text || ''
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="model-panel">
      <div className="model-panel-header" style={{ borderColor: model.color }}>
        <span className="model-panel-badge" style={{ background: model.bg, color: model.color }}>
          {model.label}
        </span>
        {isLoading  && <span className="model-panel-status">thinking…</span>}
        {isStopped  && <span className="model-panel-status" style={{ color: '#f59e0b' }}>■ stopped</span>}
        {isError    && <span className="model-panel-status model-panel-status--error">{response?.error || 'error'}</span>}
        {!isLoading && !isError && !isStopped && (
          <button className="model-panel-copy" onClick={copyText} title="Copy response">
            {copied ? '✓' : '⎘'}
          </button>
        )}

        <button
          className="wl-score-btn"
          style={scoreNum ? { color: scoreColor, borderColor: scoreColor, background: `${scoreColor}18` } : {}}
          onClick={openScorer}
          title="Submit Wet Lab Score"
          disabled={isLoading}
        >
          {scoreNum ? `🔬 ${scoreNum}/10` : '🔬 Wet Lab Score'}
        </button>
      </div>

      {wlOpen && (
        <div className="wl-scorer">
          <p className="wl-scorer-label">Rate this model (1 = poor, 10 = perfect)</p>
          <div className="wl-score-grid">
            {WL_SCORES.map(n => (
              <button
                key={n}
                className={`wl-score-num ${wlPending === n ? 'wl-score-num--active' : ''}`}
                style={wlPending === n ? { background: WL_SCORE_COLOR(n), color: '#fff', borderColor: WL_SCORE_COLOR(n) } : {}}
                onClick={() => setWlPending(n)}
              >
                {n}
              </button>
            ))}
          </div>
          {wlPending && (
            <p className="wl-score-hint" style={{ color: WL_SCORE_COLOR(wlPending) }}>
              {wlPending >= 8 ? 'Strong — prediction well supported by lab results' :
               wlPending >= 5 ? 'Moderate — partially correct or missing nuance' :
               'Weak — prediction poorly matched lab results'}
            </p>
          )}
          <textarea
            className="wl-notes"
            placeholder="Optional notes (e.g. which experiment validated this)…"
            value={wlNotes}
            onChange={e => setWlNotes(e.target.value)}
            rows={2}
          />
          <div className="wl-scorer-actions">
            {wlScore && <button className="wl-btn wl-btn--clear" onClick={clearScore}>Clear</button>}
            <button className="wl-btn wl-btn--cancel" onClick={() => setWlOpen(false)}>Cancel</button>
            <button className="wl-btn wl-btn--save" onClick={saveScore} disabled={!wlPending || wlSaving}>
              {wlSaving ? 'Saving…' : 'Save Score'}
            </button>
          </div>
        </div>
      )}

      <div className="model-panel-body">
        {isLoading ? (
          <div className="model-panel-loading">
            <span className="dot-pulse" />
            <span className="dot-pulse" style={{ animationDelay: '0.2s' }} />
            <span className="dot-pulse" style={{ animationDelay: '0.4s' }} />
          </div>
        ) : isStopped ? (
          <p className="model-panel-error" style={{ color: '#f59e0b' }}>
            Response stopped by user.
          </p>
        ) : isError ? (
          <p className="model-panel-error">
            {response.error === 'RATE_LIMIT'
              ? 'Rate limit hit — please wait a moment and retry.'
              : response.error === 'API_KEY_INVALID'
              ? 'API key invalid. Check your key in API Keys settings.'
              : response.error === 'TIMEOUT'
              ? 'Model timed out after 120 seconds.'
              : response.error === 'SERVICE_UNAVAILABLE'
              ? 'Gemini is under high demand right now. Please retry in a moment.'
              : response.error === 'MODEL_UNAVAILABLE'
              ? <>This model is temporarily busy on the free tier. Retry, or <button className="model-panel-link" onClick={() => window.dispatchEvent(new CustomEvent('omnimed:open_api_keys'))}>add your own key</button> for reliable access.</>
              : response.error === 'DAILY_LIMIT_REACHED'
              ? <>Daily free limit reached (5/day). <button className="model-panel-link" onClick={() => window.dispatchEvent(new CustomEvent('omnimed:open_api_keys'))}>Add your API key</button> to continue.</>
              : `Error: ${response.error}`}
          </p>
        ) : (
          <div
            className="model-panel-text"
            dangerouslySetInnerHTML={{ __html: safeHtml(response.text || '') }}
          />
        )}
      </div>
    </div>
  )
}

function ConclusionBlock({ responses, mode, onGenerate, conclusion, loading, activeModels }) {
  const active = MODELS.filter(m => (activeModels || []).includes(m.key))
  const allDone = active.every(m => responses[m.key] && responses[m.key].status !== undefined)

  if (!allDone || active.length === 0) return null

  const modelNames = active.map(m => m.label.split(' ')[0]).join(', ')

  return (
    <div className="conclusion-wrap">
      {!conclusion && !loading && (
        <button className="btn-conclusion" onClick={onGenerate}>
          🔍 Get Conclusion from {modelNames}
        </button>
      )}
      {loading && (
        <div className="conclusion-loading">
          <span className="dot-pulse" /><span className="dot-pulse" style={{ animationDelay: '0.2s' }} /><span className="dot-pulse" style={{ animationDelay: '0.4s' }} />
          <span style={{ marginLeft: 8, color: '#64748b', fontSize: '0.85rem' }}>Gemini synthesizing…</span>
        </div>
      )}
      {conclusion && (
        <div className="conclusion-card">
          <div className="conclusion-header">
            <span className="conclusion-badge">Conclusion</span>
          </div>
          <div
            className="conclusion-text"
            dangerouslySetInnerHTML={{ __html: safeHtml(
              typeof conclusion === 'string' ? conclusion : JSON.stringify(conclusion, null, 2)
            )}}
          />
        </div>
      )}
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
  const [selectedModels, setSelectedModels] = useState(['gemini'])
  const bottomRef = useRef(null)
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)
  const abortControllerRef = useRef(null)

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
      setSelectedModels(['gemini'])
    }
    window.addEventListener('omnimed:new_chat', handler)
    return () => window.removeEventListener('omnimed:new_chat', handler)
  }, [])

  // Scroll to bottom on new message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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
      responses: { claude: null, gpt4: null, gemini: null },
      conclusion: null,
      conclusionLoading: false,
    }

    const newMessages = [...messages, userMsg, assistantMsg]
    setMessages(newMessages)
    saveConversation(userId, sid, text.slice(0, 50), newMessages)

    // Build per-model history — same mode only, last 20 items (10 pairs)
    function buildHistory(modelKey, existingMessages, currentMode) {
      const history = []
      for (const m of existingMessages) {
        if (m.role === 'user' && m.mode === currentMode) {
          history.push({ role: 'user', content: m.content })
        } else if (m.role === 'assistant' && m.mode === currentMode) {
          const r = m.responses?.[modelKey]
          // Include completed responses and stopped ones (as partial context)
          if (r?.status === 'ok') {
            history.push({ role: 'assistant', content: r.text })
          } else if (r?.status === 'stopped') {
            history.push({ role: 'assistant', content: '[Response was stopped by user]' })
          }
        }
      }
      return history.slice(-20)
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
        { message: text, models: selectedModels, mode, session_id: sid, histories, attachments: readyAtts, system_prompt: resolvePromptContent(), user_keys: buildUserKeys() },
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

  async function getConclusion(assistantMsgId, responses, msgMode, activeModels) {
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

    try {
      const sid = sessionId || newSessionId()
      const data = await postJSON('/meta-analysis', {
        session_id: sid, model_responses: modelResponses, model_labels: modelLabels, mode: msgMode,
      })
      const conclusionText = data.meta_analysis
        ? (typeof data.meta_analysis === 'string' ? data.meta_analysis : JSON.stringify(data.meta_analysis, null, 2))
        : 'No conclusion returned.'

      setMessages(prev => {
        const updated = prev.map(m =>
          m.id === assistantMsgId
            ? { ...m, conclusion: conclusionText, conclusionLoading: false }
            : m
        )
        if (sessionId) saveConversation(userId, sessionId, '', updated)
        return updated
      })

      // Persist the conclusion onto the assistant turn (title unchanged).
      const assistantWithConclusion = {
        id: assistantMsgId, role: 'assistant', mode: msgMode,
        activeModels, responses, conclusion: conclusionText,
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

  const isEmpty = messages.length === 0

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

      {/* Messages area */}
      <div className="chat-messages">
        {isEmpty && (
          <div className="chat-welcome">
            <div className="chat-welcome-icon"><Logo size={56} /></div>
            <h2 className="chat-welcome-title">OmniMed Multi-LLM Research</h2>
            <p className="chat-welcome-sub">
              Ask anything across Claude, GPT-4, and Gemini simultaneously.<br />
              Switch to <strong>Biomedical</strong> mode for scientific context, or <strong>Research</strong> for miRNA extraction.
            </p>
          </div>
        )}

        {messages.map((msg) => {
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="chat-row chat-row--user">
                {msg.attachments?.length > 0 && (
                  <div className="chat-att-pills">
                    {msg.attachments.map((a, i) => (
                      <span key={i} className="chat-att-pill">
                        {a.file_type === 'image' ? '🖼️' : a.file_type === 'pdf' ? '📄' : '📎'} {a.filename}
                      </span>
                    ))}
                  </div>
                )}
                {msg.content && <div className="chat-bubble-user">{msg.content}</div>}
                <div className="chat-mode-tag">{MODES.find(m => m.key === msg.mode)?.icon} {msg.mode}</div>
              </div>
            )
          }

          // Assistant message
          return (
            <div key={msg.id} className="chat-row chat-row--assistant">
              <div className="model-panels-row">
                {MODELS.filter(m => (msg.activeModels || ['claude','gpt4','gemini']).includes(m.key)).map(m => (
                  <ModelPanel
                    key={m.key}
                    model={m}
                    response={msg.responses[m.key]}
                    loading={!msg.responses[m.key]}
                    msgId={msg.id}
                    sessionId={sessionId}
                  />
                  ))}
              </div>

              <ConclusionBlock
                responses={msg.responses}
                mode={msg.mode}
                conclusion={msg.conclusion}
                loading={msg.conclusionLoading}
                activeModels={msg.activeModels || []}
                onGenerate={() => getConclusion(msg.id, msg.responses, msg.mode, msg.activeModels)}
              />
            </div>
          )
        })}
        <div ref={bottomRef} />
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

          {/* Model selector toggles */}
          <div className="model-selector">
            {MODELS.map(m => {
              const active   = selectedModels.includes(m.key)
              const isGated  = m.key === 'claude' || m.key === 'gpt4'
              // Gated models are usable by everyone (5 free/day). The backend
              // enforces the limit and returns DAILY_LIMIT_REACHED, which prompts
              // for a key. Premium (own key / granted / admin) removes the badge.
              const premium  = !isGated || modelHasKey(m.key) || hasBackendAccess
              return (
                <button
                  key={m.key}
                  className={`model-toggle ${active ? 'model-toggle--on' : 'model-toggle--off'}`}
                  style={active ? { borderColor: m.color, color: m.color } : {}}
                  onClick={() => {
                    if (active && selectedModels.length === 1) return
                    setSelectedModels(prev =>
                      active ? prev.filter(k => k !== m.key) : [...prev, m.key]
                    )
                  }}
                  title={active ? `Exclude ${m.label}` : `Include ${m.label}`}
                  disabled={sending}
                >
                  {active && <span className="model-toggle-check">✓</span>}
                  {m.label.split(' ')[0]}
                  {isGated && !premium && <span className="model-toggle-badge" title="5 free/day, then add your key">5/day</span>}
                </button>
              )
            })}
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
