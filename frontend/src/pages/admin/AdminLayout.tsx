import { NavLink, Outlet } from 'react-router-dom'

export const ADMIN_TABS = [
  { key: 'users', label: '사용자' },
  { key: 'plans', label: '플랜' },
  { key: 'usage', label: '사용량' },
  { key: 'global-settings', label: '전역 설정' },
  { key: 'tools', label: '시스템 도구' },
]

/** 관리자 공통 프레임: 헤더 + 탭 바 + 탭 콘텐츠(Outlet). 탭 스타일은 설정 페이지와 동일. */
export default function AdminLayout() {
  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900">관리자</h1>
          <a href="/" className="text-sm text-blue-600 hover:underline">← 앱으로</a>
        </div>

        <div className="flex flex-wrap gap-1 border-b border-gray-200">
          {ADMIN_TABS.map((t) => (
            <NavLink
              key={t.key}
              to={`/admin/${t.key}`}
              className={({ isActive }) =>
                `px-3 py-2 text-sm rounded-t-lg -mb-px border-b-2 transition-colors ${
                  isActive
                    ? 'border-blue-600 text-blue-600 font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-800'
                }`
              }
            >
              {t.label}
            </NavLink>
          ))}
        </div>

        <Outlet />
      </div>
    </div>
  )
}
