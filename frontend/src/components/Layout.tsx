import { useState } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { useAuth } from '../auth/useAuth'
import { defaultSettingsCategory } from '../settings/defs'
import { NewGroupModal, EditGroupModal } from './GroupModals'

const NAV = [
  { sub: '', label: '대시보드', icon: '🏠', end: true },
  { sub: 'videos', label: '영상 목록', icon: '🎬' },
  { sub: 'instant-analyze', label: '즉시 분석', icon: '🔍', adminOnly: true },
  { sub: 'digests', label: '다이제스트', icon: '📊' },
  { sub: 'tags', label: '태그 클라우드', icon: '🏷' },
  { sub: 'channels', label: '채널 관리', icon: '📺' },
  { sub: 'logs', label: '작업 로그', icon: '📋' },
]

export default function Layout() {
  const { groups, activeSlug, activeGroup } = useGroup()
  const { authEnabled, user, logout } = useAuth()
  // 일반 사용자를 admin 전용 database 탭으로 보내지 않도록 역할 기반 기본 탭
  const settingsDefault = defaultSettingsCategory(user?.role)
  const navigate = useNavigate()
  const location = useLocation()
  const [groupModal, setGroupModal] = useState<null | 'new' | 'edit'>(null)
  // 즉시 분석 등 관리자 전용 메뉴는 일반 사용자에게 노출하지 않음
  const nav = NAV.filter((i) => !i.adminOnly || user?.role === 'admin')

  // 그룹 전환: 현재 페이지(첫 경로 세그먼트)를 유지하되, PK 종속 상세 경로면 대시보드로.
  const onSwitchGroup = (slug: string) => {
    const after = location.pathname.replace(/^\/g\/[^/]+/, '')
    const seg = after.split('/').filter(Boolean)[0] ?? ''
    if (seg === 'settings') {
      navigate(`/g/${slug}/settings/${settingsDefault}`)
      return
    }
    const navSubs = nav.map((i) => i.sub).filter(Boolean)
    const safe = navSubs.includes(seg) ? seg : ''
    navigate(`/g/${slug}/${safe}`)
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors whitespace-nowrap ${
      isActive ? 'bg-blue-600 text-white font-medium' : 'text-gray-700 hover:bg-gray-100'
    }`

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        {/* 1행(모바일): 타이틀 + 계정 / 데스크톱에서는 sm:contents로 펼쳐져 단일 행에 합류 */}
        <div className="flex items-center justify-between gap-3 sm:contents">
          <span className="font-bold text-gray-800 whitespace-nowrap">Youtube Monitor</span>
          {authEnabled && (
            <div className="flex items-center gap-2 sm:hidden">
              {user && <span className="text-xs text-gray-400">{user.display_name || user.email}</span>}
              {user?.role === 'admin' && (
                <a href="/admin" className="text-xs text-amber-600 hover:underline">관리자</a>
              )}
              <a href="/me" className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">마이페이지</a>
              <button onClick={logout} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">로그아웃</button>
            </div>
          )}
        </div>
        {/* 2행(모바일): 그룹 선택/수정/추가 */}
        <div className="flex flex-wrap items-center gap-2 sm:contents">
          <select
            value={activeSlug}
            onChange={(e) => onSwitchGroup(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm min-w-0"
          >
            {groups.map((g) => (
              <option key={g.slug} value={g.slug}>{g.name} ({g.slug}){g.is_active ? '' : ' ⏸'}</option>
            ))}
          </select>
          {activeGroup && !activeGroup.is_active && (
            <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-500 border border-gray-300 whitespace-nowrap">⏸ 일시정지</span>
          )}
          <button onClick={() => setGroupModal('edit')} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50 whitespace-nowrap">그룹 수정</button>
          <button onClick={() => setGroupModal('new')} className="text-xs px-2 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700 whitespace-nowrap">+ 새 그룹</button>
        </div>
        {/* 계정(데스크톱 전용): 오른쪽 끝 정렬 */}
        {authEnabled && (
          <div className="ml-auto hidden sm:flex items-center gap-2">
            {user && <span className="text-xs text-gray-400">{user.display_name || user.email}</span>}
            {user?.role === 'admin' && (
              <a href="/admin" className="text-xs text-amber-600 hover:underline">관리자</a>
            )}
            <a href="/me" className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">마이페이지</a>
            <button onClick={logout} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">로그아웃</button>
          </div>
        )}
      </header>

      <div className="flex flex-col lg:flex-row flex-1 max-w-7xl mx-auto w-full px-3 sm:px-4 py-4 gap-4 lg:gap-6">
        <aside className="w-full lg:w-52 shrink-0">
          <nav className="flex flex-row lg:flex-col gap-1 overflow-x-auto bg-white rounded-xl shadow-sm p-2 lg:p-3 lg:sticky lg:top-6">
            {nav.map((item) => (
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
              <NavLink
                to={`/g/${activeSlug}/settings/${settingsDefault}`}
                className={() => linkClass({ isActive: location.pathname.includes('/settings/') })}
              >
                <span>⚙️</span><span>설정</span>
              </NavLink>
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
