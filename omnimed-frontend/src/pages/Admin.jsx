import { useEffect } from 'react'
import { Routes, Route, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import Overview      from './admin/Overview'
import Reliability   from './admin/Reliability'
import Usage         from './admin/Usage'
import Users         from './admin/Users'
import GrantedUsers  from './admin/GrantedUsers'
import Logo        from '../components/Logo.jsx'
import './Admin.css'

export default function Admin() {
  const { user, isAdmin, signOut } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (user && !isAdmin) navigate('/', { replace: true })
  }, [user, isAdmin])

  if (!isAdmin) return null

  return (
    <div className="admin-shell">
      <header className="admin-header">
        <div className="admin-header-left">
          <span className="admin-logo"><Logo size={22} /> OmniMed</span>
          <span className="admin-badge">Admin</span>
        </div>
        <nav className="admin-nav">
          <NavLink to="/admin/overview"     className={({ isActive }) => `admin-nav-link ${isActive ? 'admin-nav-link--active' : ''}`}>Overview</NavLink>
          <NavLink to="/admin/reliability"  className={({ isActive }) => `admin-nav-link ${isActive ? 'admin-nav-link--active' : ''}`}>Model Reliability</NavLink>
          <NavLink to="/admin/usage"         className={({ isActive }) => `admin-nav-link ${isActive ? 'admin-nav-link--active' : ''}`}>Usage & Cost</NavLink>
          <NavLink to="/admin/users"         className={({ isActive }) => `admin-nav-link ${isActive ? 'admin-nav-link--active' : ''}`}>Users</NavLink>
          <NavLink to="/admin/access"        className={({ isActive }) => `admin-nav-link ${isActive ? 'admin-nav-link--active' : ''}`}>Access Control</NavLink>
        </nav>
        <div className="admin-header-right">
          <span className="admin-user-email">{user.email}</span>
          <button className="admin-back-btn" onClick={() => navigate('/')}>← Back to App</button>
          <button className="admin-signout-btn" onClick={signOut}>Sign out</button>
        </div>
      </header>

      <main className="admin-main">
        <Routes>
          <Route path="/"            element={<Navigate to="overview" replace />} />
          <Route path="overview"     element={<Overview />} />
          <Route path="reliability"  element={<Reliability />} />
          <Route path="usage"        element={<Usage />} />
          <Route path="users"        element={<Users />} />
          <Route path="access"       element={<GrantedUsers />} />
        </Routes>
      </main>
    </div>
  )
}
