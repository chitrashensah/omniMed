import { createContext, useContext, useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

const AuthContext = createContext(null)
const ADMIN_EMAIL = import.meta.env.VITE_ADMIN_EMAIL || 'chitrashenshah@gmail.com'

export function AuthProvider({ children }) {
  const [user, setUser]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [isGranted, setIsGranted] = useState(false)

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setUser(session?.user ?? null)
      if (session?.user) checkGranted(session.user)
      setLoading(false)
    })

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      const u = session?.user ?? null
      setUser(u)
      if (u) checkGranted(u)
      else setIsGranted(false)
    })

    return () => subscription.unsubscribe()
  }, [])

  async function checkGranted(u) {
    if (!u) return
    if (u.email === ADMIN_EMAIL) { setIsGranted(true); return }
    try {
      const { data } = await supabase
        .from('granted_users')
        .select('id')
        .eq('user_id', u.id)
        .limit(1)
      setIsGranted((data || []).length > 0)
    } catch {
      setIsGranted(false)
    }
  }

  const isAdmin = !!(user && user.email === ADMIN_EMAIL)
  // Whether the user can use Claude/GPT-4o without their own key
  const hasBackendAccess = !!(user && (isAdmin || isGranted))

  async function signInWithEmail(email, password) {
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }

  async function signUpWithEmail(email, password) {
    const { error } = await supabase.auth.signUp({ email, password })
    if (error) throw error
  }

  async function signInWithGoogle() {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    })
    if (error) throw error
  }

  async function signOut() {
    await supabase.auth.signOut()
  }

  return (
    <AuthContext.Provider value={{
      user, loading, isGranted, isAdmin, hasBackendAccess,
      signInWithEmail, signUpWithEmail, signInWithGoogle, signOut,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
