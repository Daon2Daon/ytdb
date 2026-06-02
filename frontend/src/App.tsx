import { useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import GroupProvider from './group/GroupProvider'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import { groupApi } from './api/groups'

// v1a 나머지 페이지는 Plan 2에서 라우트 추가.
function Placeholder({ name }: { name: string }) {
  return <div className="text-gray-500">{name} — Plan 2에서 구현 예정</div>
}

// 루트 진입: 첫 그룹으로 보정.
function RootRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    groupApi.list().then((groups) => {
      if (groups.length > 0) navigate(`/g/${groups[0].slug}/`, { replace: true })
    })
  }, [navigate])
  return <div className="min-h-screen flex items-center justify-center text-gray-400">로딩 중…</div>
}

export default function App() {
  return (
    <Routes>
      <Route path="/g/:slug" element={<GroupProvider />}>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="channels" element={<Placeholder name="채널 관리" />} />
          <Route path="videos" element={<Placeholder name="영상 목록" />} />
          <Route path="instant-analyze" element={<Placeholder name="영상 분석" />} />
          <Route path="logs" element={<Placeholder name="Logs" />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Route>
      </Route>
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  )
}
