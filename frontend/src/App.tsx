import { useEffect, useState } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import GroupProvider from './group/GroupProvider'
import Layout from './components/Layout'
import Admin from './pages/Admin'
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

// 루트 진입: 첫 그룹으로 보정. 그룹이 없거나 조회 실패 시 안내.
function RootRedirect() {
  const navigate = useNavigate()
  const [message, setMessage] = useState('로딩 중…')
  useEffect(() => {
    groupApi
      .list()
      .then((groups) => {
        if (groups.length > 0) navigate(`/g/${groups[0].slug}/`, { replace: true })
        else setMessage('운영 중인 모니터링 그룹이 없습니다. 그룹을 먼저 생성하세요.')
      })
      .catch((e) => setMessage(`그룹 목록을 불러오지 못했습니다: ${(e as Error).message}`))
  }, [navigate])
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
      <Route path="/admin" element={<Admin />} />
      <Route path="/me" element={<MyPage />} />
      <Route path="/" element={<RootRedirect />} />
      <Route path="*" element={<RootRedirect />} />
    </Routes>
  )
}
