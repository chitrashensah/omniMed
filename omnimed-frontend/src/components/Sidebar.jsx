import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import * as chatStore from '../lib/chatStore'
import * as verifyStore from '../lib/verifyStore'
import Logo from './Logo.jsx'
import './Sidebar.css'

export default function Sidebar({
  mobileOpen = false,
  onMobileClose = () => {},
  theme = 'dark',
  onThemeToggle = () => {},
}) {
  const [collapsed, setCollapsed]       = useState(false)
  const [conversations, setConversations] = useState([])
  const [editingId, setEditingId]       = useState(null)
  const [editTitle, setEditTitle]       = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [syncError, setSyncError] = useState('')
  const editInputRef = useRef(null)
  const navigate = useNavigate()
  const { sessionId } = useParams()
  const { user, isAdmin, signOut } = useAuth()

  // Re-run when the user id becomes available (auth loads async) so a fresh
  // device fetches the conversation list from Supabase once logged in.
  useEffect(() => { loadConversations() }, [user?.id])

  const convKey = `omnimed_conversations_${user?.id || 'anonymous'}`
  const msgKeyFor = (id) => `omnimed_msgs_${user?.id || 'anonymous'}_${id}`

  function refreshFromCache() {
    try {
      const stored = JSON.parse(localStorage.getItem(convKey) || '[]')
      setConversations(stored.sort((a, b) => b.updatedAt - a.updatedAt))
    } catch { /* ignore bad cache */ }
  }

  async function loadConversations() {
    refreshFromCache()  // instant: cached list
    // Source of truth: refresh from Supabase (scoped to this user), update cache.
    if (user?.id) {
      try {
        const convs = await chatStore.listConversations(user.id)
        setConversations(convs)
        localStorage.setItem(convKey, JSON.stringify(convs))
      } catch { /* offline / RLS — keep the cached list */ }
    }
  }

  useEffect(() => {
    // saveConversation fires this on every streaming update — refresh from the
    // local cache only here; Supabase is queried on mount and explicit changes.
    const handler = () => refreshFromCache()
    window.addEventListener('omnimed:conversation_updated', handler)
    return () => window.removeEventListener('omnimed:conversation_updated', handler)
  }, [])

  function newChat() {
    navigate('/')
    window.dispatchEvent(new CustomEvent('omnimed:new_chat'))
  }

  function openChat(id) {
    if (editingId === id) return
    navigate(`/chat/${id}`)
    onMobileClose()
  }

  // ── Delete ──────────────────────────────────────────────
  function requestDelete(e, id) {
    e.stopPropagation()
    setConfirmDeleteId(id)
  }

  // Removed from the list at once, but put back if the server refuses: a
  // silent failure looked deleted until the next reload brought it back.
  async function confirmDelete() {
    const id = confirmDeleteId
    const previous = conversations
    setConfirmDeleteId(null)
    setSyncError('')
    const updated = conversations.filter(c => c.id !== id)
    localStorage.setItem(convKey, JSON.stringify(updated))
    setConversations(updated)
    if (sessionId === id) navigate('/')
    try {
      await chatStore.deleteConversation(id)
      localStorage.removeItem(msgKeyFor(id))
      verifyStore.clearSession(id)       // its cached citation checks and AI scores too
    } catch (err) {
      console.error('[Sidebar] delete failed:', err)
      localStorage.setItem(convKey, JSON.stringify(previous))
      setConversations(previous)
      setSyncError('Could not delete that conversation. Please try again.')
    }
  }

  function cancelDelete() {
    setConfirmDeleteId(null)
  }

  // ── Edit title ───────────────────────────────────────────
  function startEdit(e, conv) {
    e.stopPropagation()
    setEditingId(conv.id)
    setEditTitle(conv.title || '')
    setTimeout(() => editInputRef.current?.focus(), 50)
  }

  async function saveEdit(id) {
    const title = editTitle.trim() || 'Untitled'
    const previous = conversations
    const updated = conversations.map(c => c.id === id ? { ...c, title } : c)
    localStorage.setItem(convKey, JSON.stringify(updated))
    setConversations(updated)
    setEditingId(null)
    setSyncError('')
    try {
      await chatStore.renameConversation(id, title)
    } catch (err) {
      console.error('[Sidebar] rename failed:', err)
      localStorage.setItem(convKey, JSON.stringify(previous))
      setConversations(previous)
      setSyncError('Could not rename that conversation. Please try again.')
    }
  }

  function handleEditKeyDown(e, id) {
    if (e.key === 'Enter') { e.preventDefault(); saveEdit(id) }
    if (e.key === 'Escape') { setEditingId(null) }
  }

  const deleteTarget = conversations.find(c => c.id === confirmDeleteId)

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div className="sidebar-mobile-backdrop" onClick={onMobileClose} />
      )}

      {/* Confirm delete dialog */}
      {confirmDeleteId && (
        <div className="sidebar-dialog-backdrop" onClick={cancelDelete}>
          <div className="sidebar-dialog" onClick={e => e.stopPropagation()}>
            <p className="sidebar-dialog-title">Delete conversation?</p>
            <p className="sidebar-dialog-body">
              &ldquo;{deleteTarget?.title || 'Untitled'}&rdquo; will be permanently deleted.
            </p>
            <div className="sidebar-dialog-actions">
              <button className="sidebar-dialog-btn sidebar-dialog-btn--cancel" onClick={cancelDelete}>Cancel</button>
              <button className="sidebar-dialog-btn sidebar-dialog-btn--delete" onClick={confirmDelete}>Delete</button>
            </div>
          </div>
        </div>
      )}

      <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''} ${mobileOpen ? 'sidebar--mobile-open' : ''}`}>
        {/* Top */}
        <div className="sidebar-top">
          {!collapsed && (
            <div className="sidebar-logo">
              <Logo size={22} className="logo-icon" />
              <span className="logo-text">OmniMed</span>
              <button
                className="sidebar-home-btn"
                onClick={() => navigate('/home')}
                title="Home"
                aria-label="Go to home page"
              >⌂</button>
            </div>
          )}
          <button className="sidebar-toggle" onClick={() => setCollapsed(v => !v)} title={collapsed ? 'Expand' : 'Collapse'}>
            {collapsed ? '»' : '«'}
          </button>
        </div>

        {/* New chat */}
        <button className="sidebar-new-btn" onClick={newChat} title="New chat">
          {collapsed ? '+' : '+ New Chat'}
        </button>

        {/* Conversation list */}
        {!collapsed && (
          <nav className="sidebar-nav">
            <p className="sidebar-nav-label">Recent</p>
            {syncError && (
              <p className="sidebar-error" role="alert">
                {syncError}
                <button className="sidebar-error-close" onClick={() => setSyncError('')} aria-label="Dismiss">×</button>
              </p>
            )}
            {conversations.length === 0 && (
              <p className="sidebar-empty">No conversations yet</p>
            )}
            {conversations.map(conv => (
              <div
                key={conv.id}
                className={`sidebar-item ${sessionId === conv.id ? 'sidebar-item--active' : ''}`}
                onClick={() => openChat(conv.id)}
              >
                {editingId === conv.id ? (
                  <input
                    ref={editInputRef}
                    className="sidebar-item-edit"
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    onBlur={() => saveEdit(conv.id)}
                    onKeyDown={e => handleEditKeyDown(e, conv.id)}
                    onClick={e => e.stopPropagation()}
                  />
                ) : (
                  <span
                    className="sidebar-item-title"
                    onDoubleClick={e => startEdit(e, conv)}
                    title="Double-click to rename"
                  >
                    {conv.title || 'Untitled'}
                  </span>
                )}
                <div className="sidebar-item-actions">
                  {editingId !== conv.id && (
                    <button
                      className="sidebar-item-action"
                      onClick={e => startEdit(e, conv)}
                      title="Rename"
                    >✏️</button>
                  )}
                  <button
                    className="sidebar-item-action sidebar-item-action--delete"
                    onClick={e => requestDelete(e, conv.id)}
                    title="Delete"
                  >🗑️</button>
                </div>
              </div>
            ))}
          </nav>
        )}

        {/* API Keys button */}
        {!collapsed && (
          <button className="sidebar-apikey-btn" onClick={() => window.dispatchEvent(new CustomEvent('omnimed:open_api_keys'))}>
            🔑 API Keys
          </button>
        )}

        {/* Admin link — only for admin user */}
        {!collapsed && isAdmin && (
          <button className="sidebar-admin-btn" onClick={() => navigate('/admin')}>
            ⚙ Admin Panel
          </button>
        )}

        {/* Bottom — theme toggle + profile */}
        {!collapsed && (
          <div className="sidebar-bottom">
            <button
              className="sidebar-theme-toggle"
              onClick={onThemeToggle}
              title="Toggle theme"
            >
              {theme === 'dark' ? '☀' : '◑'}
              <span style={{ fontSize: '0.75rem', marginLeft: 4 }}>
                {theme === 'dark' ? 'Light mode' : 'Dark mode'}
              </span>
            </button>
            <div className="sidebar-profile">
              <div className="sidebar-avatar">
                {user?.email?.[0]?.toUpperCase() || 'U'}
              </div>
              <div className="sidebar-profile-info">
                <span className="sidebar-profile-name" title={user?.email}>
                  {user?.email?.split('@')[0] || 'User'}
                </span>
                <span className="sidebar-profile-role" title={user?.email}>
                  {user?.email || '—'}
                </span>
              </div>
              <button className="sidebar-signout-btn" onClick={signOut} title="Sign out">⏻</button>
            </div>
          </div>
        )}
      </aside>
    </>
  )
}
