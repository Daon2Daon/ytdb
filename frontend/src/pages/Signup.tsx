import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { authApi } from '../api/auth'

export default function Signup() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== password2) {
      setError('비밀번호가 일치하지 않습니다.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await authApi.signup(token, email, password, displayName)
      window.location.href = '/' // 가입 시 자동 로그인 → 앱으로
    } catch (err) {
      setError((err as Error).message || '가입에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500 px-4 text-center">
        초대 링크가 올바르지 않습니다. 관리자에게 초대를 요청하세요.
      </div>
    )
  }

  const input =
    'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500'
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <form onSubmit={submit} className="bg-white rounded-xl shadow-sm p-6 w-full max-w-sm space-y-4">
        <h1 className="text-xl font-bold text-gray-900">초대 가입</h1>
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
        )}
        <div>
          <label className="block text-sm text-gray-600 mb-1">이메일</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            autoFocus autoComplete="email" required className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">표시 이름 (선택)</label>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">비밀번호 (8자 이상)</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password" required minLength={8} className={input} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">비밀번호 확인</label>
          <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)}
            autoComplete="new-password" required minLength={8} className={input} />
        </div>
        <button type="submit" disabled={busy}
          className="w-full bg-blue-600 text-white rounded-lg py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
          {busy ? '가입 중…' : '가입하기'}
        </button>
      </form>
    </div>
  )
}
