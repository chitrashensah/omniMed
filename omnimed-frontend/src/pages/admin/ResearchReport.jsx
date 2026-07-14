import { useState, useEffect, useRef } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ErrorBar,
} from 'recharts'
import * as XLSX from 'xlsx'
import { authedFetch } from '../../lib/api'

const MODEL_COLOR = {
  claude: '#7c3aed', gpt4: '#15803d', gemini: '#b45309',
  deepseek: '#0369a1', groq: '#6d28d9', qwen: '#be185d', cohere: '#0f766e',
}
const scoreColor = (m) => m >= 7 ? '#22c55e' : m >= 5 ? '#f59e0b' : '#f87171'

// ── Percentile helper (linear interpolation) ──
function percentile(sorted, p) {
  if (sorted.length === 1) return sorted[0]
  const idx = (sorted.length - 1) * p
  const lo = Math.floor(idx), hi = Math.ceil(idx)
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo)
}

// ── Custom SVG box plot (Recharts has no native box plot) ──
function BoxPlot({ groups }) {
  const W = 700, H = 300, padL = 40, padB = 40, padT = 16, padR = 16
  const plotW = W - padL - padR, plotH = H - padT - padB
  const yOf = (v) => padT + plotH - (v / 10) * plotH   // score 0-10 → y
  const n = groups.length
  const colW = plotW / n
  const boxW = Math.min(56, colW * 0.5)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 320 }}>
      {/* Y gridlines + labels */}
      {[0, 2, 4, 6, 8, 10].map(t => (
        <g key={t}>
          <line x1={padL} y1={yOf(t)} x2={W - padR} y2={yOf(t)} stroke="var(--border)" strokeWidth="1" opacity="0.5" />
          <text x={padL - 8} y={yOf(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">{t}</text>
        </g>
      ))}
      {groups.map((g, i) => {
        const sorted = [...g.scores].sort((a, b) => a - b)
        const q1 = percentile(sorted, 0.25), med = percentile(sorted, 0.5), q3 = percentile(sorted, 0.75)
        const lo = sorted[0], hi = sorted[sorted.length - 1]
        const cx = padL + colW * i + colW / 2
        const color = MODEL_COLOR[g.model] || '#0ea5e9'
        return (
          <g key={g.model}>
            {/* whiskers */}
            <line x1={cx} y1={yOf(hi)} x2={cx} y2={yOf(q3)} stroke={color} strokeWidth="1.5" />
            <line x1={cx} y1={yOf(lo)} x2={cx} y2={yOf(q1)} stroke={color} strokeWidth="1.5" />
            <line x1={cx - 8} y1={yOf(hi)} x2={cx + 8} y2={yOf(hi)} stroke={color} strokeWidth="1.5" />
            <line x1={cx - 8} y1={yOf(lo)} x2={cx + 8} y2={yOf(lo)} stroke={color} strokeWidth="1.5" />
            {/* box (Q1–Q3) */}
            <rect x={cx - boxW / 2} y={yOf(q3)} width={boxW} height={Math.max(1, yOf(q1) - yOf(q3))}
                  fill={color} fillOpacity="0.18" stroke={color} strokeWidth="1.5" rx="2" />
            {/* median */}
            <line x1={cx - boxW / 2} y1={yOf(med)} x2={cx + boxW / 2} y2={yOf(med)} stroke={color} strokeWidth="2.5" />
            {/* individual points, jittered */}
            {sorted.map((s, j) => (
              <circle key={j} cx={cx + (((j * 37) % 11) - 5)} cy={yOf(s)} r="2.5" fill={color} fillOpacity="0.55" />
            ))}
            {/* x label */}
            <text x={cx} y={H - padB + 20} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
              {g.label.split(' ')[0]}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

export default function ResearchReport() {
  const [report, setReport]   = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [genLoading, setGenLoading] = useState(false)
  const [copied, setCopied]   = useState(false)
  const chartRef = useRef(null)

  useEffect(() => { load(false) }, [])

  async function load(withNarrative) {
    if (withNarrative) setGenLoading(true)
    else setLoading(true)
    setError(null)
    try {
      const res = await authedFetch('/admin/research-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ narrative: withNarrative }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Failed to load report')
      setReport(prev => withNarrative ? { ...data } : data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
      setGenLoading(false)
    }
  }

  function exportExcel() {
    const rows = report.stats.descriptive.map(d => ({
      Model: d.label, N: d.n, Mean: d.mean, SD: d.sd, SEM: d.sem,
      '95% CI Low': d.ci95_low ?? '', '95% CI High': d.ci95_high ?? '',
      Median: d.median, Min: d.min, Max: d.max,
    }))
    const ws = XLSX.utils.json_to_sheet(rows)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Model Reliability')
    // Second sheet: significant pairwise
    const pw = (report.stats.pairwise || []).map(p => ({
      'Model A': p.model_a, 'Model B': p.model_b, 'Mean Diff': p.mean_diff,
      t: p.t, p: p.p, Significant: p.significant ? 'Yes' : 'No',
    }))
    if (pw.length) XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(pw), 'Pairwise Tests')
    XLSX.writeFile(wb, `omnimed-reliability-${new Date().toISOString().slice(0,10)}.xlsx`)
  }

  function exportChartPNG() {
    const svg = chartRef.current?.querySelector('svg')
    if (!svg) return
    const clone = svg.cloneNode(true)
    const { width, height } = svg.getBoundingClientRect()
    clone.setAttribute('width', width)
    clone.setAttribute('height', height)
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    // white background for publication
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    bg.setAttribute('width', width); bg.setAttribute('height', height); bg.setAttribute('fill', '#ffffff')
    clone.insertBefore(bg, clone.firstChild)
    const data = new XMLSerializer().serializeToString(clone)
    const url = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(data)))
    const img = new Image()
    img.onload = () => {
      const scale = 3
      const canvas = document.createElement('canvas')
      canvas.width = width * scale; canvas.height = height * scale
      const ctx = canvas.getContext('2d')
      ctx.scale(scale, scale)
      ctx.drawImage(img, 0, 0)
      const a = document.createElement('a')
      a.download = `omnimed-reliability-figure-${new Date().toISOString().slice(0,10)}.png`
      a.href = canvas.toDataURL('image/png')
      a.click()
    }
    img.src = url
  }

  function copyNarrative() {
    if (!report?.narrative) return
    navigator.clipboard.writeText(report.narrative).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  if (loading) return <div className="admin-loading">Loading research report…</div>
  if (error) return (
    <div className="admin-section" style={{ borderColor: 'rgba(248,113,113,0.3)' }}>
      <p style={{ color: '#f87171', fontSize: '0.85rem' }}>⚠ {error}</p>
    </div>
  )

  const S = report.stats
  const chartData = S.descriptive.map(d => ({
    label: d.label.split(' ')[0], model: d.model, mean: d.mean, sem: d.sem,
  }))

  if (S.total_validations === 0) {
    return (
      <div>
        <h1 className="admin-page-title">Research Report</h1>
        <div className="admin-section"><div className="admin-empty">
          No wet-lab validation scores yet. Once researchers score model responses,
          this page compiles publication-ready statistics, figures, and a draft Results section.
        </div></div>
      </div>
    )
  }

  return (
    <div>
      <h1 className="admin-page-title">Research Report</h1>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '-14px', marginBottom: 20 }}>
        Publication-ready summary of {S.total_validations} wet-lab validation{S.total_validations !== 1 ? 's' : ''} across {S.n_models} model{S.n_models !== 1 ? 's' : ''}.
      </p>

      {S.underpowered && (
        <div style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: 10, padding: '10px 14px', marginBottom: 20, fontSize: '0.82rem', color: '#f59e0b' }}>
          ⚠ <strong>Preliminary data:</strong> some models have fewer than {S.min_n_for_power} validations.
          Inferential tests are shown but underpowered — frame findings as a pilot in the paper.
        </div>
      )}

      {/* ── Headline figure: mean ± SEM ── */}
      <div className="admin-section" style={{ marginBottom: 24 }}>
        <div className="admin-section-header">
          <span className="admin-section-title">Figure 1 — Model Reliability (mean ± SEM, 1–10 scale)</span>
          <button className="admin-export-btn" onClick={exportChartPNG}>⬇ Figure (PNG)</button>
        </div>
        <div ref={chartRef}>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData} margin={{ top: 10, right: 20, bottom: 5, left: 0 }} barCategoryGap="30%">
              <XAxis dataKey="label" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 10]} tick={{ fontSize: 11, fill: 'var(--text-muted)' }} axisLine={false} tickLine={false}
                     label={{ value: 'Reliability score', angle: -90, position: 'insideLeft', style: { fontSize: 11, fill: 'var(--text-muted)' } }} />
              <Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
                       formatter={(v, n, p) => [`${v} ± ${p.payload.sem}`, 'Mean ± SEM']} />
              <Bar dataKey="mean" radius={[6, 6, 0, 0]}>
                {chartData.map((e, i) => <Cell key={i} fill={MODEL_COLOR[e.model] || '#0ea5e9'} />)}
                <ErrorBar dataKey="sem" width={5} strokeWidth={1.5} stroke="var(--text)" direction="y" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ── Figure 2: score distributions (box plot) ── */}
      {S.descriptive.some(d => d.n >= 2) && (
        <div className="admin-section" style={{ marginBottom: 24 }}>
          <div className="admin-section-header">
            <span className="admin-section-title">Figure 2 — Score Distributions (box plot, per model)</span>
          </div>
          <BoxPlot groups={S.descriptive.filter(d => d.scores?.length)} />
          <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 8 }}>
            Box = interquartile range (Q1–Q3); center line = median; whiskers = min/max; dots = individual validations.
          </p>
        </div>
      )}

      {/* ── Descriptive statistics table ── */}
      <div className="admin-section" style={{ marginBottom: 24 }}>
        <div className="admin-section-header">
          <span className="admin-section-title">Table 1 — Descriptive Statistics</span>
          <button className="admin-export-btn" onClick={exportExcel}>⬇ Data (Excel)</button>
        </div>
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead><tr>
              <th>Model</th><th>N</th><th>Mean</th><th>SD</th><th>SEM</th>
              <th>95% CI</th><th>Median</th><th>Range</th>
            </tr></thead>
            <tbody>
              {S.descriptive.map(d => (
                <tr key={d.model}>
                  <td><strong>{d.label}</strong></td>
                  <td>{d.n}</td>
                  <td style={{ fontWeight: 700, color: scoreColor(d.mean) }}>{d.mean.toFixed(2)}</td>
                  <td>{d.sd.toFixed(2)}</td>
                  <td>{d.sem.toFixed(2)}</td>
                  <td style={{ color: 'var(--text-muted)' }}>{d.ci95_low != null ? `[${d.ci95_low}, ${d.ci95_high}]` : '—'}</td>
                  <td>{d.median.toFixed(1)}</td>
                  <td style={{ color: 'var(--text-muted)' }}>{d.min}–{d.max}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Inferential statistics ── */}
      <div className="admin-section" style={{ marginBottom: 24 }}>
        <div className="admin-section-header">
          <span className="admin-section-title">Inferential Statistics</span>
        </div>
        {S.anova ? (
          <p style={{ fontSize: '0.85rem', color: 'var(--text)', marginBottom: 12 }}>
            <strong>One-way ANOVA:</strong> F = {S.anova.f}, p = {S.anova.p}
            {' — '}
            <span style={{ color: S.anova.significant ? '#22c55e' : 'var(--text-muted)', fontWeight: 600 }}>
              {S.anova.significant ? 'significant difference between models (p < 0.05)' : 'no significant difference (p ≥ 0.05)'}
            </span>
          </p>
        ) : (
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 12 }}>
            ANOVA not computable yet — needs at least two models with ≥ 2 validations each.
          </p>
        )}
        {(S.pairwise || []).length > 0 && (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead><tr><th>Comparison</th><th>Mean Δ</th><th>t</th><th>p</th><th>Significant*</th></tr></thead>
              <tbody>
                {S.pairwise.map((p, i) => (
                  <tr key={i}>
                    <td>{p.model_a} vs {p.model_b}</td>
                    <td>{p.mean_diff > 0 ? '+' : ''}{p.mean_diff}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{p.t}</td>
                    <td style={{ color: 'var(--text-muted)' }}>{p.p}</td>
                    <td style={{ fontWeight: 700, color: p.significant ? '#22c55e' : 'var(--text-muted)' }}>
                      {p.significant ? 'Yes' : 'No'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 8 }}>
              *Bonferroni-corrected for multiple comparisons. Welch's t-test (unequal variances).
            </p>
          </div>
        )}
      </div>

      {/* ── AI-generated narrative ── */}
      <div className="admin-section">
        <div className="admin-section-header">
          <span className="admin-section-title">Draft Results Section (AI-generated)</span>
          {report.narrative && (
            <button className="admin-export-btn" onClick={copyNarrative}>
              {copied ? 'Copied ✓' : '⎘ Copy'}
            </button>
          )}
        </div>

        {!report.narrative && !genLoading && (
          <div style={{ textAlign: 'center', padding: '20px 0' }}>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 14 }}>
              Generate a formal Results-section write-up and a thematic analysis of reviewer notes,
              drafted from the statistics above. Review and edit before use in a manuscript.
            </p>
            <button className="admin-export-btn" onClick={() => load(true)} style={{ padding: '9px 20px' }}>
              ✦ Generate Draft
            </button>
          </div>
        )}

        {genLoading && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Generating academic write-up…
          </div>
        )}

        {report.narrative && (
          <>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: '0.88rem', lineHeight: 1.7, color: 'var(--text)', fontFamily: 'var(--font-body)' }}>
              {report.narrative}
            </div>
            <button className="admin-filter-clear" onClick={() => load(true)} style={{ marginTop: 14 }}>
              ↻ Regenerate
            </button>
          </>
        )}
        {report.narrative_error && (
          <p style={{ color: '#f87171', fontSize: '0.8rem', marginTop: 10 }}>
            Narrative generation failed: {report.narrative_error}
          </p>
        )}
        <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          AI-drafted from your data — verify all figures and claims before publication.
        </p>
      </div>
    </div>
  )
}
