import { useState, useEffect } from 'react'
import { KEY_CONFIGS, getKey, setKey, removeKey } from '../lib/apiKeys'
import './ApiKeyModal.css'

const TABS = [
  { id: 'openrouter', label: 'OpenRouter (Recommended)' },
  { id: 'claude',     label: 'Anthropic' },
  { id: 'gpt4',       label: 'OpenAI' },
  { id: 'gemini',     label: 'Google (Gemini)' },
  { id: 'deepseek',   label: 'DeepSeek' },
  { id: 'groq',       label: 'Groq' },
  { id: 'qwen',       label: 'Qwen' },
  { id: 'cohere',     label: 'Cohere' },
]

export default function ApiKeyModal({ onClose, initialTab = 'openrouter' }) {
  const [activeTab, setActiveTab] = useState(initialTab)
  const [keyValues, setKeyValues] = useState({})
  const [saved, setSaved]         = useState({})
  const [visible, setVisible]     = useState({})

  useEffect(() => {
    const vals = {}
    for (const k of Object.keys(KEY_CONFIGS)) {
      vals[k] = getKey(k) || ''
    }
    setKeyValues(vals)
  }, [])

  function handleSave(provider) {
    const val = (keyValues[provider] || '').trim()
    setKey(provider, val || null)
    setSaved(s => ({ ...s, [provider]: true }))
    setTimeout(() => setSaved(s => ({ ...s, [provider]: false })), 2000)
  }

  function handleRemove(provider) {
    removeKey(provider)
    setKeyValues(v => ({ ...v, [provider]: '' }))
  }

  const cfg = KEY_CONFIGS[activeTab]
  const hasKey = !!(keyValues[activeTab]?.trim())
  const isStored = !!(getKey(activeTab))

  return (
    <div className="apikey-backdrop" onClick={onClose}>
      <div className="apikey-modal" onClick={e => e.stopPropagation()}>
        <div className="apikey-header">
          <span className="apikey-title">API Keys</span>
          <button className="apikey-close" onClick={onClose}>✕</button>
        </div>

        <div className="apikey-body">
          {/* Sidebar tabs */}
          <div className="apikey-tabs">
            {TABS.map(t => (
              <button
                key={t.id}
                className={`apikey-tab ${activeTab === t.id ? 'apikey-tab--active' : ''}`}
                onClick={() => setActiveTab(t.id)}
              >
                {getKey(t.id) && <span className="apikey-dot" />}
                {t.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="apikey-content">
            <div className="apikey-provider-header">
              <span className="apikey-provider-name">{cfg.label}</span>
              <span className="apikey-provider-desc">{cfg.description}</span>
            </div>

            {/* How to get key */}
            <div className="apikey-steps">
              <p className="apikey-steps-title">How to get your key:</p>
              <ol className="apikey-steps-list">
                {cfg.steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            </div>

            {/* Key input */}
            <div className="apikey-input-row">
              <div className="apikey-input-wrap">
                <input
                  type={visible[activeTab] ? 'text' : 'password'}
                  className="apikey-input"
                  value={keyValues[activeTab] || ''}
                  onChange={e => setKeyValues(v => ({ ...v, [activeTab]: e.target.value }))}
                  placeholder={cfg.placeholder}
                  spellCheck={false}
                  autoComplete="off"
                />
                <button
                  className="apikey-toggle-vis"
                  onClick={() => setVisible(v => ({ ...v, [activeTab]: !v[activeTab] }))}
                  tabIndex={-1}
                >
                  {visible[activeTab] ? 'Hide' : 'Show'}
                </button>
              </div>
              <button
                className={`apikey-save-btn ${saved[activeTab] ? 'apikey-save-btn--saved' : ''}`}
                onClick={() => handleSave(activeTab)}
                disabled={!hasKey}
              >
                {saved[activeTab] ? 'Saved ✓' : 'Save'}
              </button>
              {isStored && (
                <button className="apikey-remove-btn" onClick={() => handleRemove(activeTab)}>
                  Remove
                </button>
              )}
            </div>

            {isStored && (
              <p className="apikey-stored-note">
                ✓ Key saved in your browser. It is never sent to our servers.
              </p>
            )}

            <p className="apikey-privacy-note">
              Keys are stored only in your browser's localStorage and sent directly to the AI provider with each request.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
