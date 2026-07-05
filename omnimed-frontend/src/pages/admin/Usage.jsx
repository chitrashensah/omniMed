import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import * as XLSX from 'xlsx'
import { supabase } from '../../lib/supabase'

const MODEL_LABELS = {
  claude: 'Claude', gpt4: 'GPT-4o', gemini: 'Gemini',
  deepseek: 'DeepSeek', groq: 'Groq', qwen: 'Qwen', cohere: 'Cohere',
}
const MODEL_COLOR = {
  claude: '#7c3aed', gpt4: '#15803d', gemini: '#b45309',
  deepseek: '#0369a1', groq: '#6d28d9', qwen: '#be185d', cohere: '#0f766e',
}

// Approx USD price per 1M tokens (input / output). Used for cost estimates only.
const PRICING = {
  claude:   { in: 3.00,  out: 15.00 },
  gpt4:     { in: 2.50,  out: 10.00 },
  gemini:   { in: 0.075, out: 0.30  },
  deepseek: { in: 0.27,  out: 1.10  },
  groq:     { in: 0.59,  out: 0.79  },
  qwen:     { in: 0.23,  out: 0.40  },
  cohere:   { in: 2.50,  out: 10.00 },
}

function estCost(model, inTok, outTok) {
  const p = PRICING[model]
  if (!p) return 0
  return (inTok / 1e6) * p.in + (outTok / 1e6) * p.out
}

const fmt = (n) => n.toLocaleString()
const usd = (n) => `$${n.toFixed(n < 1 ? 4 : 2)}`

export default function Usage() {
  const [rows, setRows]       = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo]     = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const params = {}
      if (dateFrom) params.p_from = new Date(dateFrom).toISOString()
      if (dateTo)   params.p_to   = new Date(dateTo + 'T23:59:59').toISOString()
      const { data, error: err } = await supabase.rpc('admin_usage_summary', params)
      if (err) throw err
      setRows(data || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const enriched = rows.map(r => {
    const inTok  = Number(r.input_tokens) || 0
    const outTok = Number(r.output_tokens) || 0
    return {
      model: r.model,
      label: MODEL_LABELS[r.model] || r.model,
      calls: Number(r.calls) || 0,
      inTok, outTok,
      totalTok: inTok + outTok,
      cacheRead: Number(r.cache_read_tokens) || 0,
      cost: estCost(r.model, inTok, outTok),
    }
  }).sort((a, b) => b.cost - a.cost)

  const totals = enriched.reduce((acc, r) => ({
    calls: acc.calls + r.calls,
    tokens: acc.tokens + r.totalTok,
    cost: acc.cost + r.cost,
  }), { calls: 0, tokens: 0, cost: 0 })

  const chartData = enriched.map(r => ({ label: r.label, model: r.model, Tokens: r.totalTok }))

  function exportExcel() {
    const data = enriched.map(r => ({
      Model: r.label,
      Calls: r.calls,
      'Input Tokens': r.inTok,
      'Output Tokens': r.outTok,
      'Cached Tokens': r.cacheRead,
      'Total Tokens': r.totalTok,
      'Est. Cost (USD)': Number(r.cost.toFixed(4)),
    }))
    const ws = XLSX.utils.json_to_sheet(data)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Usage')
    XLSX.writeFile(wb, `omnimed-usage-${new Date().toISOString().slice(0, 10)}.xlsx`)
  }

  function clearFilters() { setDateFrom(''); setDateTo(''); setTimeout(load, 0) }

  if (loading) return <div className="admin-loading">Loading usage…</div>

  if (error) return (
    <div className="admin-section" style={{ borderColor: 'rgba(248,113,113,0.3)' }}>
      <p style={{ color: '#f87171', fontSize: '0.85rem' }}>⚠ Failed to load usage: <code>{error}</code></p>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: 8 }}>
        Make sure the <code>usage_logs</code> table and <code>admin_usage_summary</code> function exist (run the full SQL).
      </p>
    </div>
  )

  return (
    <div>
      <h1 className="admin-page-title">Usage & Cost</h1>

      <div className="admin-stat-grid">
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total API Calls</span>
          <span className="admin-stat-value">{fmt(totals.calls)}</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Total Tokens</span>
          <span className="admin-stat-value" style={{ color: '#0ea5e9' }}>{fmt(totals.tokens)}</span>
        </div>
        <div className="admin-stat-card">
          <span className="admin-stat-label">Est. Total Cost</span>
          <span className="admin-stat-value" style={{ color: '#22c55e' }}>{usd(totals.cost)}</span>
          <span className="admin-stat-sub">across all models</span>
        </div>
      </div>

      {chartData.length > 0 && (
        <div className="admin-section" style={{ marginBottom: 24 }}>
          <div className="admin-section-header">
            <span className="admin-section-title">Tokens per Model</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartData} barCategoryGap="35%">
              <XAxis dataKey="label" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} tickFormatter={(v) => v >= 1000 ? `${(v/1000).toFixed(0)}k` : v} />
              <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }} formatter={(v) => [fmt(v), 'Tokens']} />
              <Bar dataKey="Tokens" radius={[6, 6, 0, 0]}>
                {chartData.map((e, i) => <Cell key={i} fill={MODEL_COLOR[e.model] || '#0ea5e9'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">Per-Model Breakdown</span>
          <div className="admin-filters">
            <span className="admin-filter-label">From</span>
            <input className="admin-date-input" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            <span className="admin-filter-label">To</span>
            <input className="admin-date-input" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
            <button className="admin-export-btn" onClick={load}>Apply</button>
            {(dateFrom || dateTo) && <button className="admin-filter-clear" onClick={clearFilters}>Clear</button>}
          </div>
          <button className="admin-export-btn" onClick={exportExcel} disabled={enriched.length === 0}>⬇ Export Excel</button>
        </div>

        {enriched.length === 0 ? (
          <div className="admin-empty">No usage recorded yet.</div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Input</th>
                  <th>Output</th>
                  <th>Cached</th>
                  <th>Total</th>
                  <th>Est. Cost</th>
                </tr>
              </thead>
              <tbody>
                {enriched.map(r => (
                  <tr key={r.model}>
                    <td><strong>{r.label}</strong></td>
                    <td>{fmt(r.calls)}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{fmt(r.inTok)}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{fmt(r.outTok)}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{fmt(r.cacheRead)}</td>
                    <td>{fmt(r.totalTok)}</td>
                    <td style={{ fontWeight: 700, color: '#22c55e' }}>{usd(r.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 12 }}>
          Costs are estimates based on public per-token pricing and exclude free-tier allowances.
        </p>
      </div>
    </div>
  )
}
