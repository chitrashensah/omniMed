import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import Logo from '../components/Logo.jsx'
import './Landing.css'

const MODELS = [
  { name: 'Claude Sonnet 4.6', org: 'Anthropic' },
  { name: 'GPT-4o',           org: 'OpenAI' },
  { name: 'Gemini 2.5 Flash', org: 'Google' },
  { name: 'DeepSeek V3',      org: 'DeepSeek' },
  { name: 'Llama 3.3 70B',    org: 'Groq' },
  { name: 'Qwen 2.5 72B',     org: 'OpenRouter' },
  { name: 'Command R+',       org: 'Cohere' },
]

const FEATURES = [
  {
    icon: '🧬',
    title: 'Multi-LLM Querying',
    body: 'Send one question to seven frontier models at once and compare their answers side by side — no more tab-hopping between chatbots.',
    highlight: true,
  },
  {
    icon: '🔑',
    title: 'Free + Bring Your Own Key',
    body: 'Five capable models are free for everyone. Add your own OpenRouter, Anthropic, or OpenAI key to unlock unlimited Claude and GPT-4o.',
    highlight: true,
  },
  {
    icon: '🔬',
    title: 'Wet-Lab Validation',
    body: 'Score each model 1–10 against real qPCR and experimental outcomes, building a reliability profile that grows more accurate over time.',
    highlight: true,
  },
  {
    icon: '⚖️',
    title: 'Consensus Synthesis',
    body: 'Gemini reads every model’s response and produces a unified conclusion that surfaces agreements, flags conflicts, and ranks candidates.',
  },
  {
    icon: '🔎',
    title: 'Live Literature Search',
    body: 'Claude, GPT-4o, and Gemini search the web for recent publications so predictions stay grounded in the latest cardiovascular research.',
  },
  {
    icon: '📝',
    title: 'Custom Prompts',
    body: 'Pick a built-in research prompt or write and save your own — per mode — so every model answers exactly the way your work demands.',
  },
  {
    icon: '📄',
    title: 'Document Uploads',
    body: 'Drop in PDFs, Word docs, CSVs, or figures and let every model reason over your source papers and datasets directly in the chat.',
  },
  {
    icon: '📊',
    title: 'Reliability Analytics',
    body: 'An admin dashboard charts per-model accuracy, tracks token usage and cost, and exports your full research record to Excel in one click.',
  },
]

const STEPS = [
  { n: '01', title: 'Ask',      body: 'Pose a miRNA / ncRNA question in biomedical mode and broadcast it to all seven models simultaneously.' },
  { n: '02', title: 'Compare',  body: 'Read structured candidate profiles side by side, then let Gemini synthesize a single consensus answer.' },
  { n: '03', title: 'Validate', body: 'Run the candidates in the lab, score each model on the outcome, and watch the reliability profiles sharpen.' },
]

const STATS = [
  { value: '7',        label: 'AI models queried in parallel' },
  { value: '1–10',     label: 'Wet-lab reliability scale' },
  { value: 'Days→Min', label: 'Literature review, compressed' },
]

/* Reveal-on-scroll: adds .is-visible when an element enters the viewport. */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll('.reveal')
    if (!('IntersectionObserver' in window)) {
      els.forEach(el => el.classList.add('is-visible'))
      return
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add('is-visible')
          io.unobserve(e.target)
        }
      })
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' })
    els.forEach(el => io.observe(el))
    return () => io.disconnect()
  }, [])
}

export default function Landing() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [theme, setTheme] = useState(() => localStorage.getItem('omnimed_theme') || 'dark')

  useReveal()

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    localStorage.setItem('omnimed_theme', next)
  }

  const goPrimary = () => navigate(user ? '/' : '/login')
  const primaryLabel = user ? 'Open app →' : 'Sign in'

  return (
    <div className="lp">
      {/* ── Nav ── */}
      <header className="lp-nav">
        <div className="lp-nav-inner">
          <a className="lp-brand" href="#top">
            <Logo size={26} className="lp-brand-mark" />
            <span className="lp-brand-name">OmniMed</span>
          </a>
          <nav className="lp-nav-links">
            <a href="#models">Models</a>
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
          </nav>
          <div className="lp-nav-actions">
            <button
              className="lp-theme-toggle"
              onClick={toggleTheme}
              title="Toggle theme"
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? '☀' : '◑'}
            </button>
            <button className="lp-nav-cta" onClick={goPrimary}>
              {primaryLabel}
            </button>
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="lp-hero" id="top">
        <div className="lp-hero-bg" aria-hidden="true">
          <div className="lp-grid" />
          <div className="lp-mesh lp-mesh-1" />
          <div className="lp-mesh lp-mesh-2" />
          <div className="lp-mesh lp-mesh-3" />
          {[...Array(16)].map((_, i) => (
            <span key={i} className={`lp-particle lp-particle-${i % 6}`} />
          ))}
        </div>

        <div className="lp-hero-content">
          <div className="lp-badge reveal">
            <span className="lp-badge-dot" />
            UDiscover Research 2026 · University of South Dakota
          </div>

          <h1 className="lp-title reveal" style={{ transitionDelay: '60ms' }}>
            One question.<br />
            <span className="lp-title-grad">Seven minds.</span>
          </h1>

          <p className="lp-subtitle reveal" style={{ transitionDelay: '120ms' }}>
            OmniMed is a multi-LLM framework for discovering therapeutic microRNA and
            non-coding RNA candidates in cardiovascular disease — cross-validated against
            real wet-lab results.
          </p>

          <div className="lp-cta-row reveal" style={{ transitionDelay: '180ms' }}>
            <button className="lp-btn lp-btn-primary" onClick={goPrimary}>
              {user ? 'Open the app →' : 'Start researching →'}
            </button>
            <a className="lp-btn lp-btn-ghost" href="#how">
              See how it works
            </a>
          </div>

          <div className="lp-hero-meta reveal" style={{ transitionDelay: '240ms' }}>
            <span>Faculty mentor · Dr. William Chen</span>
            <span className="lp-dot-sep">•</span>
            <span>Built by Chitrashen Sah</span>
          </div>
        </div>
      </section>

      {/* ── Model strip ── */}
      <section className="lp-models reveal" id="models">
        <p className="lp-models-label">Querying the frontier, all at once</p>
        <div className="lp-models-grid">
          {MODELS.map((m, i) => (
            <div className="lp-model-chip reveal" key={m.name} style={{ transitionDelay: `${i * 50}ms` }}>
              <span className="lp-model-name">{m.name}</span>
              <span className="lp-model-org">{m.org}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ── */}
      <section className="lp-section" id="features">
        <div className="lp-section-head reveal">
          <span className="lp-eyebrow">Capabilities</span>
          <h2 className="lp-h2">Everything a research session needs</h2>
          <p className="lp-section-sub">
            From the first question to lab-validated conclusions, OmniMed keeps the whole
            discovery loop in one place.
          </p>
        </div>
        <div className="lp-feature-grid">
          {FEATURES.map((f, i) => (
            <article
              className={`lp-feature reveal ${f.highlight ? 'lp-feature--highlight' : ''}`}
              key={f.title}
              style={{ transitionDelay: `${(i % 3) * 80}ms` }}
            >
              {f.highlight && <span className="lp-feature-flag">Featured</span>}
              <div className="lp-feature-icon">{f.icon}</div>
              <h3 className="lp-feature-title">{f.title}</h3>
              <p className="lp-feature-body">{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="lp-section lp-how" id="how">
        <div className="lp-section-head reveal">
          <span className="lp-eyebrow">Workflow</span>
          <h2 className="lp-h2">Ask · Compare · Validate</h2>
          <p className="lp-section-sub">
            A simple three-step loop that turns days of manual literature review into minutes.
          </p>
        </div>
        <div className="lp-steps">
          {STEPS.map((s, i) => (
            <div className="lp-step reveal" key={s.n} style={{ transitionDelay: `${i * 90}ms` }}>
              <div className="lp-step-n">{s.n}</div>
              <h3 className="lp-step-title">{s.title}</h3>
              <p className="lp-step-body">{s.body}</p>
              {i < STEPS.length - 1 && <div className="lp-step-arrow" aria-hidden="true">→</div>}
            </div>
          ))}
        </div>
      </section>

      {/* ── Stats ── */}
      <section className="lp-stats">
        {STATS.map((s, i) => (
          <div className="lp-stat reveal" key={s.label} style={{ transitionDelay: `${i * 80}ms` }}>
            <div className="lp-stat-value">{s.value}</div>
            <div className="lp-stat-label">{s.label}</div>
          </div>
        ))}
      </section>

      {/* ── CTA band ── */}
      <section className="lp-cta-band">
        <div className="lp-cta-band-inner reveal">
          <h2 className="lp-cta-title">Ready to put seven models to work?</h2>
          <p className="lp-cta-sub">
            {user
              ? 'Jump back into your workspace and start a new multi-LLM research session.'
              : 'Create a free account and run your first multi-LLM research session in minutes.'}
          </p>
          <button className="lp-btn lp-btn-primary lp-btn-lg" onClick={goPrimary}>
            {user ? 'Open the app →' : 'Get started →'}
          </button>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="lp-footer">
        <div className="lp-footer-inner">
          <div className="lp-footer-brand">
            <Logo size={24} className="lp-brand-mark" />
            <span className="lp-brand-name">OmniMed</span>
          </div>
          <p className="lp-footer-note">
            Multi-LLM Biomedical Research Framework · Funded by the University of South Dakota
            UDiscover Undergraduate Research Scholarship (2026).
          </p>
          <p className="lp-footer-fine">
            © {new Date().getFullYear()} OmniMed · For academic and research use.
          </p>
        </div>
      </footer>
    </div>
  )
}
