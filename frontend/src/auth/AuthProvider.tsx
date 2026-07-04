import { useCallback, useEffect, useState } from 'react'
import { authApi, type MeResponse } from '../api/auth'
import { setUnauthorizedHandler } from '../api/http'
import Spinner from '../components/Spinner'
import Login from '../pages/Login'
import { AuthContext } from './useAuth'

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<MeResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setState(await authApi.me())
    } catch {
      // /me 자체가 실패하면 로그인 필요로 간주.
      setState({ auth_enabled: true, authenticated: false, user: null })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // 세션 만료(임의 API 401) 시 로그인 화면으로 전환.
  useEffect(() => {
    setUnauthorizedHandler(() =>
      setState((s) => (s ? { ...s, authenticated: false, user: null } : s)),
    )
    return () => setUnauthorizedHandler(null)
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      /* 무시 */
    }
    setState((s) => (s ? { ...s, authenticated: false, user: null } : s))
  }, [])

  if (loading || !state) return <Spinner />

  if (state.auth_enabled && !state.authenticated) {
    return <Login onLoggedIn={refresh} />
  }

  return (
    <AuthContext.Provider value={{ user: state.user, authEnabled: state.auth_enabled, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
