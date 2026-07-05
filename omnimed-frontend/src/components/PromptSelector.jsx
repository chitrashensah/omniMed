import { useState, useEffect, useRef } from 'react'
import { supabase } from '../lib/supabase'
import { useAuth } from '../context/AuthContext'
import { BUILTIN_PROMPTS, PROMPTS_BY_MODE } from '../data/builtinPrompts'
import './PromptSelector.css'

export default function PromptSelector({ mode, selectedPromptId, onSelect }) {
  const { user } = useAuth()
  const [open, setOpen]               = useState(false)
  const [userPrompts, setUserPrompts] = useState([])
  const [modal, setModal]             = useState(null) // null | 'create' | 'edit'
  const [modalData, setModalData]     = useState({ id: null, name: '', content: '', mode, isBuiltin: false, defaultContent: '' })
  const [saving, setSaving]           = useState(false)
  const dropRef = useRef(null)

  const builtins    = PROMPTS_BY_MODE(mode)
  const myPrompts   = userPrompts.filter(p => p.mode === mode)
  const otherPrompts = userPrompts.filter(p => p.mode !== mode)
  const allPrompts  = [...builtins, ...userPrompts]
  const selected    = allPrompts.find(p => p.id === selectedPromptId) || null

  useEffect(() => {
    if (user) loadUserPrompts()
  }, [user])

  useEffect(() => {
    function handler(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  async function loadUserPrompts() {
    const { data } = await supabase.from('user_prompts').select('*').order('created_at')
    setUserPrompts(data || [])
  }

  function openCreate() {
    setModalData({ id: null, name: '', content: '', mode, isBuiltin: false, defaultContent: '' })
    setModal('create')
    setOpen(false)
  }

  function openEdit(p, isBuiltin = false) {
    setModalData({
      id: p.id,
      name: p.name,
      content: p.content,
      mode: p.mode || mode,
      isBuiltin,
      defaultContent: isBuiltin ? p.content : '',
    })
    setModal('edit')
    setOpen(false)
  }

  function openCopy(p) {
    const targetMode = p.mode === 'biomedical' ? 'normal' : 'biomedical'
    setModalData({ id: null, name: p.name + ' (copy)', content: p.content, mode: targetMode, isBuiltin: false, defaultContent: '' })
    setModal('create')
    setOpen(false)
  }

  async function saveModal() {
    if (!modalData.name.trim() || !modalData.content.trim()) return
    setSaving(true)
    try {
      if (modal === 'edit' && modalData.id && !modalData.isBuiltin) {
        await supabase.from('user_prompts')
          .update({ name: modalData.name, content: modalData.content, mode: modalData.mode })
          .eq('id', modalData.id).eq('user_id', user.id)
      } else if (modal === 'edit' && modalData.isBuiltin) {
        // Save edited builtin as a custom prompt override with same id-style name
        await supabase.from('user_prompts').insert({
          user_id: user.id,
          name: modalData.name,
          content: modalData.content,
          mode: modalData.mode,
        })
      } else {
        await supabase.from('user_prompts').insert({
          user_id: user.id,
          name: modalData.name,
          content: modalData.content,
          mode: modalData.mode,
        })
      }
      await loadUserPrompts()
      setModal(null)
    } finally {
      setSaving(false)
    }
  }

  async function deletePrompt(id) {
    await supabase.from('user_prompts').delete().eq('id', id).eq('user_id', user.id)
    if (selectedPromptId === id) onSelect(null)
    await loadUserPrompts()
  }

  function selectPrompt(id) {
    onSelect(selectedPromptId === id ? null : id)
    setOpen(false)
  }

  function closeModal() { setModal(null) }

  return (
    <div className="prompt-selector" ref={dropRef}>
      <button
        className={`prompt-trigger ${selected ? 'prompt-trigger--active' : ''}`}
        onClick={() => setOpen(v => !v)}
        title="Choose system prompt"
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
          <path d="M2 2h12v2H2zM2 6h8v2H2zM2 10h10v2H2z"/>
        </svg>
        {selected ? selected.name : 'Prompt'}
        <span className="prompt-trigger-arrow">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="prompt-dropdown">
          {/* None option */}
          <button
            className={`prompt-item prompt-item--none ${!selectedPromptId ? 'prompt-item--active' : ''}`}
            onClick={() => { onSelect(null); setOpen(false) }}
          >
            <span className="prompt-item-name">None</span>
            <span className="prompt-item-sub">Model answers naturally</span>
          </button>

          {/* Built-ins */}
          <div className="prompt-section-label">Built-in</div>
          {builtins.map(p => (
            <div key={p.id} className={`prompt-item ${selectedPromptId === p.id ? 'prompt-item--active' : ''}`}>
              <div className="prompt-item-main" onClick={() => selectPrompt(p.id)}>
                <span className="prompt-item-name">
                  <svg className="prompt-lock-icon" width="10" height="12" viewBox="0 0 10 12" fill="currentColor">
                    <rect x="1" y="5" width="8" height="7" rx="1.5"/>
                    <path d="M3 5V3.5a2 2 0 014 0V5"/>
                  </svg>
                  {p.name}
                </span>
              </div>
              <button className="prompt-item-btn" onClick={() => openEdit(p, true)}>Edit</button>
              <button className="prompt-item-btn" onClick={() => openCopy(p)}>Copy</button>
            </div>
          ))}

          {/* My Prompts */}
          {myPrompts.length > 0 && (
            <>
              <div className="prompt-section-label">My Prompts</div>
              {myPrompts.map(p => (
                <div key={p.id} className={`prompt-item ${selectedPromptId === p.id ? 'prompt-item--active' : ''}`}>
                  <div className="prompt-item-main" onClick={() => selectPrompt(p.id)}>
                    <span className="prompt-item-name">{p.name}</span>
                  </div>
                  <button className="prompt-item-btn" onClick={() => openEdit(p, false)}>Edit</button>
                  <button className="prompt-item-btn" onClick={() => openCopy(p)}>Copy</button>
                  <button className="prompt-item-btn prompt-item-btn--del" onClick={() => deletePrompt(p.id)}>Delete</button>
                </div>
              ))}
            </>
          )}

          {/* From other mode */}
          {otherPrompts.length > 0 && (
            <>
              <div className="prompt-section-label">From Other Mode</div>
              {otherPrompts.map(p => (
                <div key={p.id} className="prompt-item">
                  <div className="prompt-item-main">
                    <span className="prompt-item-name" style={{ color: 'var(--text-muted)' }}>{p.name}</span>
                    <span className="prompt-item-sub">{p.mode}</span>
                  </div>
                  <button className="prompt-item-btn" onClick={() => openCopy(p)}>Copy here</button>
                </div>
              ))}
            </>
          )}

          <div className="prompt-dropdown-footer">
            <button className="prompt-create-btn" onClick={openCreate}>+ Create New Prompt</button>
          </div>
        </div>
      )}

      {/* Modal */}
      {modal && (
        <div className="prompt-modal-backdrop" onClick={closeModal}>
          <div className="prompt-modal" onClick={e => e.stopPropagation()}>
            <div className="prompt-modal-header">
              <span className="prompt-modal-title">
                {modal === 'create' ? 'Create New Prompt' : `Edit — ${modalData.name}`}
              </span>
              <button className="prompt-modal-close" onClick={closeModal}>✕</button>
            </div>

            <div className="prompt-modal-field">
              <label>Name</label>
              <input
                type="text"
                value={modalData.name}
                onChange={e => setModalData(d => ({ ...d, name: e.target.value }))}
                placeholder="e.g. Ferroptosis Focus"
                maxLength={60}
              />
            </div>

            <div className="prompt-modal-field">
              <div className="prompt-modal-field-header">
                <label>System Prompt</label>
                {modalData.isBuiltin && (
                  <button
                    className="prompt-reset-btn"
                    onClick={() => setModalData(d => ({ ...d, content: d.defaultContent }))}
                    title="Reset to default"
                  >
                    Reset to default
                  </button>
                )}
              </div>
              <textarea
                value={modalData.content}
                onChange={e => setModalData(d => ({ ...d, content: e.target.value }))}
                rows={12}
                placeholder="Enter your system prompt here…"
              />
            </div>

            {modal === 'create' && (
              <div className="prompt-modal-mode-row">
                <label>Mode</label>
                <div className="prompt-mode-btns">
                  {['normal', 'biomedical'].map(m => (
                    <button
                      key={m}
                      className={`prompt-mode-btn ${modalData.mode === m ? 'prompt-mode-btn--active' : ''}`}
                      onClick={() => setModalData(d => ({ ...d, mode: m }))}
                    >
                      {m === 'normal' ? '💬 Normal' : '🔬 Biomedical'}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {modalData.isBuiltin && (
              <p className="prompt-modal-note">
                Saving will create a new custom prompt with your edits. The original built-in is unchanged.
              </p>
            )}

            <div className="prompt-modal-actions">
              <button className="prompt-modal-cancel" onClick={closeModal}>Cancel</button>
              <button
                className="prompt-modal-save"
                onClick={saveModal}
                disabled={saving || !modalData.name.trim() || !modalData.content.trim()}
              >
                {saving ? 'Saving…' : modalData.isBuiltin ? 'Save as New Prompt' : modal === 'edit' ? 'Save Changes' : 'Create Prompt'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
