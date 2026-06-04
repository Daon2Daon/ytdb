import { useState } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { useAuth } from '../auth/useAuth'
import { SETTING_CATEGORIES } from '../settings/defs'
import { NewGroupModal, EditGroupModal } from './GroupModals'

const NAV = [
  { sub: '', label: '대시보드', icon: '🏠', end: true },
  { sub: 'channels', label: '채널 관리', icon: '📺' },
  { sub: 'videos', label: '영상 목록', icon: '🎬' },
  { sub: 'instant-analyze', label: '영상 분석', icon: '🔍' },
  { sub: 'tags', label: '태그 클라우드', icon: '🏷' },
  { sub: 'digests', label: '주간 리뷰', icon: '📊' },
  { sub: 'logs', label: 'Logs', icon: '📋' },
]

export default function Layout() {
  const { groups, activeSlug, activeGroup } = useGroup()
  const { authEnabled, username, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [groupModal, setGroupModal] = useState<null | 'new' | 'edit'>(null)

  // 그룹 전환: 현재 페이지(첫 경로 세그먼트)를 유지하되, PK 종속 상세 경로면 대시보드로.
  const onSwitchGroup = (slug: string) => {
    const after = location.pathname.replace(/^\/g\/[^/]+/, '')
    const seg = after.split('/').filter(Boolean)[0] ?? ''
    const safe = ['channels', 'videos', 'instant-analyze', 'tags', 'digests', 'logs'].includes(seg) ? seg : ''
    navigate(`/g/${slug}/${safe}`)
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors whitespace-nowrap ${
      isActive ? 'bg-blue-600 text-white font-medium' : 'text-gray-700 hover:bg-gray-100'
    }`

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center gap-3">
        <span className="font-bold text-gray-800">Youtube Monitor</span>
        <select
          value={activeSlug}
          onChange={(e) => onSwitchGroup(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
        >
          {groups.map((g) => (
            <option key={g.slug} value={g.slug}>{g.name} ({g.slug}){g.is_active ? '' : ' ⏸'}</option>
          ))}
        </select>
        {activeGroup && !activeGroup.is_active && (
          <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-500 border border-gray-300">⏸ 일시정지</span>
        )}
        <button onClick={() => setGroupModal('edit')} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">그룹 수정</button>
        <button onClick={() => setGroupModal('new')} className="text-xs px-2 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700">+ 새 그룹</button>
        {authEnabled && (
          <div className="ml-auto flex items-center gap-2">
            {username && <span className="text-xs text-gray-400">{username}</span>}
            <button onClick={logout} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">로그아웃</button>
          </div>
        )}
      </header>

      <div className="flex flex-col lg:flex-row flex-1 max-w-7xl mx-auto w-full px-3 sm:px-4 py-4 gap-4 lg:gap-6">
        <aside className="w-full lg:w-52 shrink-0">
          <nav className="flex flex-row lg:flex-col gap-1 overflow-x-auto bg-white rounded-xl shadow-sm p-2 lg:p-3 lg:sticky lg:top-6">
            {NAV.map((item) => (
              <NavLink
                key={item.sub}
                to={`/g/${activeSlug}/${item.sub}`}
                end={item.end}
                className={linkClass}
              >
                <span>{item.icon}</span>
                <span>{item.label}</span>
              </NavLink>
            ))}
            <div className="mt-1 pt-1 border-t border-gray-100 flex flex-row lg:flex-col gap-1">
              <span className="px-3 py-1 text-[11px] font-semibold text-gray-400 uppercase">설정</span>
              {SETTING_CATEGORIES.map((c) => (
                <NavLink key={c.key} to={`/g/${activeSlug}/settings/${c.key}`} className={linkClass}>
                  <span>⚙️</span><span>{c.label}</span>
                </NavLink>
              ))}
            </div>
          </nav>
        </aside>
        <main className="flex-1 min-w-0 w-full">
          <Outlet />
        </main>
      </div>
      {groupModal === 'new' && <NewGroupModal onClose={() => setGroupModal(null)} />}
      {groupModal === 'edit' && <EditGroupModal onClose={() => setGroupModal(null)} />}
    </div>
  )
}
