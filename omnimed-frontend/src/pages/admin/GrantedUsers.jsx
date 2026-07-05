import { useState, useEffect } from 'react'
import { supabase } from '../../lib/supabase'
import { authedFetch } from '../../lib/api'

export default function GrantedUsers() {
  const [users, setUsers]     = useState([])
  const [loading, setLoading] = useState(true)
  const [email, setEmail]     = useState('')
  const [searching, setSearching] = useState(false)
  const [found, setFound]     = useState(null)
  const [error, setError]     = useState(null)
  const [granting, setGranting] = useState(false)

  useEffect(() => { loadGranted() }, [])

  async function loadGranted() {
    setLoading(true)
    try {
      const res = await authedFetch('/admin/granted-users')
      const data = await res.json()
      setUsers(data.users || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function searchUser() {
    if (!email.trim()) return
    setSearching(true)
    setFound(null)
    setError(null)
    try {
      const { data } = await supabase
        .from('profiles')
        .select('id, email, created_at')
        .ilike('email', email.trim())
        .limit(1)
      if (!data || data.length === 0) {
        setError('No user found with that email.')
      } else {
        setFound(data[0])
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setSearching(false)
    }
  }

  async function grantAccess() {
    if (!found) return
    setGranting(true)
    try {
      await authedFetch('/admin/granted-users', {
        method: 'POST',
        body: JSON.stringify({ user_id: found.id, email: found.email }),
      })
      setFound(null)
      setEmail('')
      await loadGranted()
    } catch (e) {
      setError(e.message)
    } finally {
      setGranting(false)
    }
  }

  async function revokeAccess(userId) {
    try {
      await authedFetch(`/admin/granted-users/${userId}`, { method: 'DELETE' })
      await loadGranted()
    } catch (e) {
      setError(e.message)
    }
  }

  if (loading) return <div className="admin-loading">Loading…</div>

  return (
    <div>
      <h1 className="admin-page-title">Access Control</h1>

      {/* Grant access */}
      <div className="admin-section" style={{ marginBottom: 24 }}>
        <div className="admin-section-header">
          <span className="admin-section-title">Grant Claude & GPT-4o Access</span>
        </div>
        <p style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: 14 }}>
          Granted users bypass the daily free limit and use the backend API keys for Claude and GPT-4o.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <input
            className="admin-date-input"
            style={{ flex: 1, minWidth: 240, padding: '8px 12px', fontSize: '0.85rem' }}
            type="email"
            placeholder="Search by email address…"
            value={email}
            onChange={e => { setEmail(e.target.value); setFound(null); setError(null) }}
            onKeyDown={e => e.key === 'Enter' && searchUser()}
          />
          <button className="admin-export-btn" onClick={searchUser} disabled={searching || !email.trim()}>
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>

        {error && <p style={{ color: '#f87171', fontSize: '0.8rem', marginTop: 8 }}>{error}</p>}

        {found && (
          <div style={{ marginTop: 12, padding: '12px 14px', background: 'var(--bg-secondary)', borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <p style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text)' }}>{found.email}</p>
              <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Joined {new Date(found.created_at).toLocaleDateString()}</p>
            </div>
            <button className="admin-export-btn" onClick={grantAccess} disabled={granting}>
              {granting ? 'Granting…' : 'Grant Access'}
            </button>
          </div>
        )}
      </div>

      {/* Granted users list */}
      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">Granted Users ({users.length})</span>
        </div>

        {users.length === 0 ? (
          <div className="admin-empty">No users have been granted access yet.</div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Granted</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id}>
                    <td><strong>{u.email || '—'}</strong></td>
                    <td style={{ color: 'var(--text-muted)' }}>{new Date(u.granted_at).toLocaleDateString()}</td>
                    <td>
                      <button
                        onClick={() => revokeAccess(u.user_id)}
                        style={{ padding: '4px 12px', border: '1px solid rgba(248,113,113,0.4)', borderRadius: 7, background: 'transparent', color: '#f87171', fontSize: '0.78rem', cursor: 'pointer', fontFamily: 'var(--font-body)' }}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
