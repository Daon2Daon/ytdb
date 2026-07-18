import { useEffect, useState } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import GroupProvider from './group/GroupProvider'
import Layout from './components/Layout'
import AdminLayout from './pages/admin/AdminLayout'
import UsersTab from './pages/admin/UsersTab'
import PlansTab from './pages/admin/PlansTab'
import UsageTab from './pages/admin/UsageTab'
import GlobalSettingsTab from './pages/admin/GlobalSettingsTab'
import ToolsTab from './pages/admin/ToolsTab'
import MyPage from './pages/MyPage'
import Dashboard from './pages/Dashboard'
import Channels from './pages/Channels'
import Videos from './pages/Videos'
import VideoDetail from './pages/VideoDetail'
import InstantAnalyze from './pages/InstantAnalyze'
import Tags from './pages/Tags'
import Logs from './pages/Logs'
import Digests from './pages/Digests'
import DigestDetail from './pages/DigestDetail'
import Settings from './pages/Settings'
import { groupApi } from './api/groups'
import { meApi } from './api/me'
import { OnboardingCard } from './components/OnboardingChecklist'

// 그룹 0개 랜딩: 온보딩 카드 + 그룹 생성 폼. GroupProvider 바깥이라 컨텍스트 훅 사용 불가.
function ZeroGroupLanding() {
  const navigate = useNavigate()
  const [destinationCount, setDestinationCount] = useState(0)
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    meApi
      .telegramDestinations()
      .then((ds) => alive && setDestinationCount(ds.length))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const create = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      // 비관리자는 서버가 slug/schema를 자동 생성(미전송). 관리자는 slug가 유효해야 함.
      const created = await groupApi.create({ slug: slug.trim() || undefined, name: name.trim() })
      navigate(`/g/${created.slug}/`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md space-y-4">
        <OnboardingCard state={{ groupCount: 0, channelCount: 0, destinationCount }} />
        <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
          <h2 className="text-lg font-semibold text-gray-800">모니터링 그룹 만들기</h2>
          {err && <p className="text-sm text-red-600">{err}</p>}
          <div>
            <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="투자 모니터"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (관리자만, 선택)</label>
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="invest"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
            />
          </div>
          <button
            onClick={create}
            disabled={busy || !name.trim()}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
          >
            {busy ? '생성 중...' : '그룹 만들기'}
          </button>
        </div>
      </div>
    </div>
  )
}

// 루트 진입: 첫 그룹으로 보정. 그룹이 없으면 온보딩 랜딩, 조회 실패 시 안내.
function RootRedirect() {
  const navigate = useNavigate()
  const [state, setState] = useState<'loading' | 'zero' | 'error'>('loading')
  const [message, setMessage] = useState('로딩 중…')
  useEffect(() => {
    groupApi
      .list()
      .then((groups) => {
        if (groups.length > 0) navigate(`/g/${groups[0].slug}/`, { replace: true })
        else setState('zero')
      })
      .catch((e) => {
        setMessage(`그룹 목록을 불러오지 못했습니다: ${(e as Error).message}`)
        setState('error')
      })
  }, [navigate])
  if (state === 'zero') return <ZeroGroupLanding />
  return <div className="min-h-screen flex items-center justify-center text-gray-500 px-4 text-center">{message}</div>
}

export default function App() {
  return (
    <Routes>
      <Route path="/g/:slug" element={<GroupProvider />}>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="channels" element={<Channels />} />
          <Route path="videos" element={<Videos />} />
          <Route path="videos/:videoPk" element={<VideoDetail />} />
          <Route path="instant-analyze" element={<InstantAnalyze />} />
          <Route path="tags" element={<Tags />} />
          <Route path="logs" element={<Logs />} />
          <Route path="digests" element={<Digests />} />
          <Route path="digests/:digestPk" element={<DigestDetail />} />
          <Route path="settings/:category" element={<Settings />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Route>
      </Route>
      <Route path="/admin" element={<AdminLayout />}>
        <Route index element={<Navigate to="users" replace />} />
        <Route path="users" element={<UsersTab />} />
        <Route path="plans" element={<PlansTab />} />
        <Route path="usage" element={<UsageTab />} />
        <Route path="global-settings" element={<GlobalSettingsTab />} />
        <Route path="tools" element={<ToolsTab />} />
        <Route path="*" element={<Navigate to="users" replace />} />
      </Route>
      <Route path="/me" element={<MyPage />} />
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  )
}
