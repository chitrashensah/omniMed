import { useState, useEffect } from 'react'
import { supabase } from '../../lib/supabase'

function timeAgo(ts) {
  const diff = (Date.now() - new Date(ts)) / 1000
  if (diff < 60)    return `${Math.floor(diff)}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(ts).toLocaleDateString()
}

const scoreColor = (v) => {
  const n = Number(v)
  if (!n) return '#0ea5e9'
  if (n >= 8) return '#22c55e'
  if (n >= 5) return '#f59e0b'
  return '#f87171'
}

export default function Overview() {
  const [stats, setStats]     = useState({ sessions: 0, validations: 0, comparisons: 0, users: 0 })
  const [activity, setActivity] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  useEffect(() => {
    async function load() {
      try {
        const [
          { count: sessions,    error: e1 },
          { count: validations, error: e2 },
          { count: comparisons, error: e3 },
        ] = await Promise.all([
          supabase.from('sessions').select('*',    { count: 'exact', head: true }),
          supabase.from('validations').select('*', { count: 'exact', head: true }),
          supabase.from('comparisons').select('*', { count: 'exact', head: true }),
        ])

        if (e1 || e2 || e3) {
          setError((e1 || e2 || e3).message)
          setLoading(false)
          return
        }

        // Recent activity — use only columns that exist in original schema
        const { data: recent, error: e4 } = await supabase
          .from('validations')
          .select('id, created_at, researcher_notes, verdict, submitted_by')
          .order('created_at', { ascending: false })
          .limit(10)

        if (e4) {
          // fallback without new columns
          const { data: recentFallback } = await supabase
            .from('validations')
            .select('id, created_at, researcher_notes')
            .order('created_at', { ascending: false })
            .limit(10)
          setActivity(recentFallback || [])
        } else {
          setActivity(recent || [])
        }

        // Unique users — try user_id first, fallback to submitted_by
        let uniqueUsers = 0
        const { data: userRows, error: e5 } = await supabase
          .from('validations')
          .select('submitted_by')
          .not('submitted_by', 'is', null)

        if (!e5 && userRows) {
          uniqueUsers = new Set(userRows.map(r => r.submitted_by)).size
        }

        setStats({
          sessions:    sessions    || 0,
          validations: validations || 0,
          comparisons: comparisons || 0,
          users:       uniqueUsers,
        })
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) return <div className="admin-loading">Loading overview…</div>

  if (error) return (
    <div className="admin-section" style={{ borderColor: 'rgba(248,113,113,0.3)' }}>
      <p style={{ color: '#f87171', fontSize: '0.85rem' }}>
        ⚠ Failed to load data: <code>{error}</code>
      </p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: 8 }}>
        Make sure you've run the updated SQL in Supabase and that RLS policies are applied.
      </p>
    </div>
  )

  return (
    <div>
      <h1 className="admin-page-title">Overview</h1>

      <div className="admin-stat-grid">
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total Sessions</span>
          <span className="admin-stat-value">{stats.sessions}</span>
          <span className="admin-stat-sub">Research sessions started</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Wet Lab Scores</span>
          <span className="admin-stat-value" style={{ color: '#0ea5e9' }}>{stats.validations}</span>
          <span className="admin-stat-sub">Model validations submitted</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Comparisons</span>
          <span className="admin-stat-value" style={{ color: '#6366f1' }}>{stats.comparisons}</span>
          <span className="admin-stat-sub">Meta-analyses run</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Active Users</span>
          <span className="admin-stat-value" style={{ color: '#22c55e' }}>{stats.users}</span>
          <span className="admin-stat-sub">Unique users who scored</span>
        </div>
      </div>

      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">Recent Activity</span>
        </div>
        {activity.length === 0 ? (
          <div className="admin-empty">No activity yet — wet lab scores will appear here.</div>
        ) : (
          <div className="admin-feed">
            {activity.map((item, i) => (
              <div key={i} className="admin-feed-item">
                <div
                  className="admin-feed-dot"
                  style={{ background: scoreColor(item.verdict) }}
                />
                <span className="admin-feed-text">
                  <strong>{item.submitted_by || 'A researcher'}</strong>
                  {' '}submitted a wet lab score
                  {item.verdict && (
                    <> — <strong style={{ color: scoreColor(item.verdict) }}>{item.verdict}/10</strong></>
                  )}
                </span>
                <span className="admin-feed-time">{timeAgo(item.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
