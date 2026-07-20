import { useEffect, useState } from 'react'
import { adminApi, type AdminGroup } from '../../api/admin'

/** 전체 그룹 열람 (운영용). 사이드바는 본인 소유만 보여주므로 타 사용자
 *  그룹 확인·지원은 이 탭에서 한다. '열기'는 해당 그룹 화면으로 이동. */
export default function GroupsTab() {
  const [groups, setGroups] = useState<AdminGroup[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    adminApi.groups().then(setGroups).catch((e) => setError((e as Error).message))
  }, [])

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-500">
        모든 사용자의 그룹입니다. 사이드바에는 본인 소유 그룹만 표시됩니다 —
        다른 사용자의 그룹을 확인하려면 여기서 열어주세요.
      </p>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
      <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="px-3 py-2">이름</th>
              <th className="px-3 py-2">영문 ID</th>
              <th className="px-3 py-2">소유자</th>
              <th className="px-3 py-2">스키마</th>
              <th className="px-3 py-2">상태</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {groups && groups.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-4 text-center text-gray-400">그룹이 없습니다.</td></tr>
            )}
            {groups?.map((g) => (
              <tr key={g.group_id} className="border-b last:border-0">
                <td className="px-3 py-2 font-medium text-gray-800">{g.name}</td>
                <td className="px-3 py-2 font-mono text-xs text-gray-600">{g.slug}</td>
                <td className="px-3 py-2">{g.owner_email ?? <span className="text-gray-400">미지정</span>}</td>
                <td className="px-3 py-2 font-mono text-xs text-gray-600">{g.schema_name}</td>
                <td className="px-3 py-2">
                  {g.is_active
                    ? <span className="text-green-600">활성</span>
                    : <span className="text-gray-400">비활성</span>}
                </td>
                <td className="px-3 py-2 text-right">
                  <a href={`/g/${g.slug}/`} className="text-blue-600 hover:underline">열기</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
