import { useState, useEffect, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  RadarChart, Radar, PolarGrid, PolarAngleAxis,
} from 'recharts'
import * as XLSX from 'xlsx'
import { supabase } from '../../lib/supabase'

const MODEL_LABELS = { claude: 'Claude', gpt4: 'GPT-4o', gemini: 'Gemini', deepseek: 'DeepSeek', groq: 'Groq', qwen: 'Qwen', cohere: 'Cohere' }
const scoreColor = (avg) => avg >= 7 ? '#22c55e' : avg >= 5 ? '#f59e0b' : '#f87171'
const PAGE_SIZE = 50

// from/to ISO bounds for the query + RPC params
function bounds(dateFrom, dateTo) {
  return {
    from: dateFrom ? new Date(dateFrom).toISOString() : null,
    to:   dateTo   ? new Date(dateTo + 'T23:59:59').toISOString() : null,
  }
}

export default function Reliability() {
  const [chartData, setChartData] = useState([])
  const [usage, setUsage]         = useState([])
  const [rows, setRows]           = useState([])
  const [total, setTotal]         = useState(0)
  const [page, setPage]           = useState(0)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)
  const [dateFrom, setDateFrom]   = useState('')
  const [dateTo, setDateTo]       = useState('')

  // DB-side aggregates (charts + token usage) — tiny payloads, no row dumps
  const loadAggregates = useCallback(async () => {
    const { from, to } = bounds(dateFrom, dateTo)
    const [rel, use] = await Promise.all([
      supabase.rpc('admin_reliability_summary', { p_from: from, p_to: to }),
      supabase.rpc('admin_usage_summary',       { p_from: from, p_to: to }),
    ])
    if (rel.error) { setError(rel.error.message); return }
    setChartData((rel.data || []).map(r => ({
      model: MODEL_LABELS[r.model] || r.model,
      'Avg Score': r.avg_score != null ? parseFloat(Number(r.avg_score).toFixed(1)) : 0,
      total: r.n,
    })))
    setUsage(use.error ? [] : (use.data || []))
  }, [dateFrom, dateTo])

  // One page of the raw scores table — server-side filter + pagination
  const loadPage = useCallback(async (pageIndex) => {
    const { from, to } = bounds(dateFrom, dateTo)
    let q = supabase
      .from('validations')
      .select('id, created_at, model, verdict, researcher_notes, submitted_by', { count: 'exact' })
      .not('verdict', 'is', null)
      .order('created_at', { ascending: false })
      .range(pageIndex * PAGE_SIZE, pageIndex * PAGE_SIZE + PAGE_SIZE - 1)
    if (from) q = q.gte('created_at', from)
    if (to)   q = q.lte('created_at', to)
    const { data, error: err, count } = await q
    if (err) { setError(err.message); return }
    setRows(data || [])
    setTotal(count || 0)
  }, [dateFrom, dateTo])

  useEffect(() => {
    setLoading(true)
    setPage(0)
    Promise.all([loadAggregates(), loadPage(0)]).finally(() => setLoading(false))
  }, [loadAggregates, loadPage])

  function goPage(next) {
    const p = Math.max(0, Math.min(next, Math.floor(Math.max(0, total - 1) / PAGE_SIZE)))
    setPage(p)
    loadPage(p)
  }

  function clearFilters() { setDateFrom(''); setDateTo('') }

  // Export pulls all matching rows on demand (explicit, not on every render)
  async function exportExcel() {
    const { from, to } = bounds(dateFrom, dateTo)
    let q = supabase
      .from('validations')
      .select('created_at, model, verdict, researcher_notes, submitted_by, session_id, msg_id')
      .not('verdict', 'is', null)
      .order('created_at', { ascending: false })
    if (from) q = q.gte('created_at', from)
    if (to)   q = q.lte('created_at', to)
    const { data } = await q
    const out = (data || []).map(r => ({
      Date:           new Date(r.created_at).toLocaleString(),
      Model:          MODEL_LABELS[r.model] || r.model || '—',
      Verdict:        r.verdict || '—',
      Notes:          r.researcher_notes || '—',
      'Submitted By': r.submitted_by || '—',
      'Session ID':   r.session_id || '—',
      'Message ID':   r.msg_id || '—',
    }))
    const ws = XLSX.utils.json_to_sheet(out)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Wet Lab Scores')
    XLSX.writeFile(wb, `omnimed-reliability-${new Date().toISOString().slice(0, 10)}.xlsx`)
  }

  if (loading) return <div className="admin-loading">Loading reliability data…</div>

  if (error) return (
    <div className="admin-section" style={{ borderColor: 'rgba(248,113,113,0.3)' }}>
      <p style={{ color: '#f87171', fontSize: '0.85rem' }}>⚠ Failed to load: <code>{error}</code></p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: 8 }}>
        Make sure you've run the latest <code>supabase_setup.sql</code> (it adds the
        <code> admin_reliability_summary</code>, <code>admin_usage_summary</code> functions and <code>usage_logs</code> table).
      </p>
    </div>
  )

  const hasScores = chartData.length > 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const fmt = (n) => Number(n || 0).toLocaleString()

  return (
    <div>
      <h1 className="admin-page-title">Model Reliability</h1>

      {!hasScores ? (
        <div className="admin-section">
          <div className="admin-empty">
            No wet lab scores yet. Scores will appear here once researchers validate model responses.
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 20, marginBottom: 24 }}>
          <div className="admin-section">
            <div className="admin-section-header">
              <span className="admin-section-title">Average Score per Model (out of 10)</span>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={chartData} barCategoryGap="35%">
                <XAxis dataKey="model" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 10]} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} formatter={(v) => [`${v}/10`, 'Avg Score']} />
                <Bar dataKey="Avg Score" radius={[6, 6, 0, 0]}>
                  {chartData.map((entry, i) => (
                    <Cell key={i} fill={scoreColor(entry['Avg Score'])} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="admin-section">
            <div className="admin-section-header">
              <span className="admin-section-title">Reliability Radar</span>
            </div>
            <ResponsiveContainer width="100%" height={240}>
              <RadarChart data={chartData}>
                <PolarGrid stroke="var(--border)" />
                <PolarAngleAxis dataKey="model" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
                <Radar name="Avg Score" dataKey="Avg Score" stroke="#0ea5e9" fill="#0ea5e9" fillOpacity={0.25} />
                <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} formatter={(v) => [`${v}/10`, 'Avg Score']} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Token usage / cost accounting (server-side aggregate) */}
      <div className="admin-section" style={{ marginBottom: 24 }}>
        <div className="admin-section-header">
          <span className="admin-section-title">Token Usage by Model</span>
        </div>
        {usage.length === 0 ? (
          <div className="admin-empty">
            No usage logged yet. Token counts appear here after model calls (requires the <code>usage_logs</code> table).
          </div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Model</th><th>Calls</th><th>Input</th><th>Output</th>
                  <th>Cache read</th><th>Cache write</th>
                </tr>
              </thead>
              <tbody>
                {usage.map(u => (
                  <tr key={u.model}>
                    <td><strong>{MODEL_LABELS[u.model] || u.model || '—'}</strong></td>
                    <td>{fmt(u.calls)}</td>
                    <td>{fmt(u.input_tokens)}</td>
                    <td>{fmt(u.output_tokens)}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{fmt(u.cache_read_tokens)}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{fmt(u.cache_write_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">All Wet Lab Scores ({total})</span>
          <div className="admin-filters">
            <span className="admin-filter-label">From</span>
            <input className="admin-date-input" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            <span className="admin-filter-label">To</span>
            <input className="admin-date-input" type="date" value={dateTo}   onChange={e => setDateTo(e.target.value)} />
            {(dateFrom || dateTo) && <button className="admin-filter-clear" onClick={clearFilters}>Clear</button>}
          </div>
          <button className="admin-export-btn" onClick={exportExcel} disabled={total === 0}>⬇ Export Excel</button>
        </div>

        {rows.length === 0 ? (
          <div className="admin-empty">No scored rows match the current filter.</div>
        ) : (
          <>
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Model</th>
                    <th>Verdict</th>
                    <th>Notes</th>
                    <th>Submitted By</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr key={row.id}>
                      <td style={{ whiteSpace: 'nowrap' }}>{new Date(row.created_at).toLocaleDateString()}</td>
                      <td><strong>{MODEL_LABELS[row.model] || row.model || '—'}</strong></td>
                      <td>
                        {row.verdict ? (
                          <span style={{
                            fontWeight: 700,
                            color: scoreColor(Number(row.verdict)),
                            background: `${scoreColor(Number(row.verdict))}18`,
                            padding: '2px 10px',
                            borderRadius: 99,
                            fontSize: '0.78rem',
                          }}>
                            {row.verdict}/10
                          </span>
                        ) : '—'}
                      </td>
                      <td style={{ color: 'var(--text-muted)', maxWidth: 260 }}>{row.researcher_notes || '—'}</td>
                      <td style={{ color: 'var(--text-muted)' }}>{row.submitted_by || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="admin-pager">
              <button className="admin-filter-clear" onClick={() => goPage(page - 1)} disabled={page === 0}>← Prev</button>
              <span className="admin-filter-label">Page {page + 1} of {totalPages}</span>
              <button className="admin-filter-clear" onClick={() => goPage(page + 1)} disabled={page + 1 >= totalPages}>Next →</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
