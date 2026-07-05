import { useState, useEffect } from 'react'
import * as XLSX from 'xlsx'
import { supabase } from '../../lib/supabase'

function timeAgo(ts) {
  if (!ts) return '—'
  const diff = (Date.now() - new Date(ts)) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(ts).toLocaleDateString()
}

export default function Users() {
  const [users, setUsers]     = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    async function load() {
      try {
        // Pull all profiles (admin RLS allows this)
        const { data: profiles, error: e1 } = await supabase
          .from('profiles')
          .select('id, email, created_at, last_seen')
          .order('created_at', { ascending: false })

        if (e1) throw e1

        // Count sessions per user_id
        const { data: sessions, error: e2 } = await supabase
          .from('sessions')
          .select('user_id')
          .not('user_id', 'is', null)

        // Count scores per submitted_by email
        const { data: validations, error: e3 } = await supabase
          .from('validations')
          .select('submitted_by, verdict')
          .not('verdict', 'is', null)

        const sessionCounts = {}
        for (const s of sessions || []) {
          sessionCounts[s.user_id] = (sessionCounts[s.user_id] || 0) + 1
        }

        const scoreCounts = {}
        for (const v of validations || []) {
          if (v.submitted_by) {
            scoreCounts[v.submitted_by] = (scoreCounts[v.submitted_by] || 0) + 1
          }
        }

        const enriched = (profiles || []).map(p => ({
          ...p,
          sessions: sessionCounts[p.id] || 0,
          scores:   scoreCounts[p.email] || 0,
        }))

        setUsers(enriched)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  function exportExcel() {
    const data = users.map(u => ({
      Email:            u.email,
      'Joined':         new Date(u.created_at).toLocaleString(),
      Sessions:         u.sessions,
      'Wet Lab Scores': u.scores,
      'Last Seen':      u.last_seen ? new Date(u.last_seen).toLocaleString() : '—',
    }))
    const ws = XLSX.utils.json_to_sheet(data)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Users')
    XLSX.writeFile(wb, `omnimed-users-${new Date().toISOString().slice(0, 10)}.xlsx`)
  }

  if (loading) return <div className="admin-loading">Loading users…</div>

  if (error) return (
    <div className="admin-section" style={{ borderColor: 'rgba(248,113,113,0.3)' }}>
      <p style={{ color: '#f87171', fontSize: '0.85rem' }}>⚠ Failed to load users: <code>{error}</code></p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: 8 }}>
        Make sure you've run the updated SQL (including the profiles table).
      </p>
    </div>
  )

  return (
    <div>
      <h1 className="admin-page-title">Users</h1>

      <div className="admin-stat-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', maxWidth: 560 }}>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total Users</span>
          <span className="admin-stat-value">{users.length}</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total Sessions</span>
          <span className="admin-stat-value">{users.reduce((s, u) => s + u.sessions, 0)}</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total Scores</span>
          <span className="admin-stat-value">{users.reduce((s, u) => s + u.scores, 0)}</span>
        </div>
      </div>

      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">All Users ({users.length})</span>
          <button className="admin-export-btn" onClick={exportExcel} disabled={users.length === 0}>⬇ Export Excel</button>
        </div>

        {users.length === 0 ? (
          <div className="admin-empty">No users yet.</div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Joined</th>
                  <th>Sessions</th>
                  <th>Wet Lab Scores</th>
                  <th>Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td><strong>{u.email || '—'}</strong></td>
                    <td style={{ color: 'var(--text-muted)' }}>{new Date(u.created_at).toLocaleDateString()}</td>
                    <td>{u.sessions}</td>
                    <td>{u.scores}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{timeAgo(u.last_seen)}</td>
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
