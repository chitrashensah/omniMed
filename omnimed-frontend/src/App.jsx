import { useState, useEffect, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Sidebar from './components/Sidebar.jsx'
import Chat from './pages/Chat.jsx'
import Login from './pages/Login.jsx'

// Heavy, rarely-hit routes are code-split so normal users don't download
// the admin panel (Recharts + xlsx) or the marketing landing page up front.
const Admin   = lazy(() => import('./pages/Admin.jsx'))
const Landing = lazy(() => import('./pages/Landing.jsx'))

function FullScreenLoader() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg)', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
      Loading…
    </div>
  )
}

function AppShell() {
  const { user, loading } = useAuth()
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('omnimed_theme') || 'dark')

  // One-time migration: copy old unscoped conversations to user-scoped keys
  useEffect(() => {
    if (!user?.id) return
    const oldConvKey = 'omnimed_conversations'
    const newConvKey = `omnimed_conversations_${user.id}`
    const oldData = localStorage.getItem(oldConvKey)
    const newData = localStorage.getItem(newConvKey)
    if (oldData && !newData) {
      localStorage.setItem(newConvKey, oldData)
      try {
        const convs = JSON.parse(oldData)
        for (const conv of convs) {
          const msgs = localStorage.getItem(`omnimed_msgs_${conv.id}`)
          if (msgs) localStorage.setItem(`omnimed_msgs_${user.id}_${conv.id}`, msgs)
        }
      } catch {}
    }
  }, [user?.id])

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--bg)', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Loading…</div>

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    localStorage.setItem('omnimed_theme', next)
    document.documentElement.setAttribute('data-theme', next)
  }

  return (
    <div className="app-shell">
      <Sidebar
        mobileOpen={mobileSidebarOpen}
        onMobileClose={() => setMobileSidebarOpen(false)}
        theme={theme}
        onThemeToggle={toggleTheme}
      />
      <main className="app-main">
        <button
          className="mobile-menu-btn"
          onClick={() => setMobileSidebarOpen(true)}
          aria-label="Open menu"
        >☰</button>
        <Routes>
          <Route path="/" element={<Chat />} />
          <Route path="/chat/:sessionId" element={<Chat />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>

      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppRouter />
    </AuthProvider>
  )
}

function AppRouter() {
  const { user, loading } = useAuth()

  if (loading) return <FullScreenLoader />

  // Logged out: public landing page, with the sign-in form at /login.
  if (!user) {
    return (
      <Suspense fallback={<FullScreenLoader />}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="*"      element={<Landing />} />
        </Routes>
      </Suspense>
    )
  }

  // Logged in: the app shell, plus the admin panel and the home/landing page.
  return (
    <Suspense fallback={<FullScreenLoader />}>
      <Routes>
        <Route path="/home"    element={<Landing />} />
        <Route path="/admin/*" element={<Admin />} />
        <Route path="*"        element={<AppShell />} />
      </Routes>
    </Suspense>
  )
}
