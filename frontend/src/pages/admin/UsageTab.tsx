import { useCallback, useEffect, useState } from 'react'
import { adminApi, type AdminUsageResponse } from '../../api/admin'

/** AI 사용량 + YouTube 쿼터 (모니터링 성격). */
export default function UsageTab() {
  const [usage, setUsage] = useState<AdminUsageResponse | null>(null)
  const [usageWindow, setUsageWindow] = useState('this_month')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setUsage(await adminApi.usage(usageWindow))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [usageWindow])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <select
          value={usageWindow}
          onChange={(e) => setUsageWindow(e.target.value)}
          className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
        >
          <option value="this_month">이번 달</option>
          <option value="last_month">지난달</option>
          <option value="30d">최근 30일</option>
        </select>
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
      {usage && usage.null_cost_row_count > 0 && (
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          단가 미등록 호출 {usage.null_cost_row_count}건 — 전역 설정 탭의 ai_model_prices에 단가를 등록하세요.
        </p>
      )}
      {usage?.youtube && (
        <div className="bg-white rounded-xl shadow-sm p-4 space-y-2">
          <h3 className="text-sm font-semibold text-gray-800">
            YouTube 쿼터 (PT {usage.youtube.usage_date} · 한도 {usage.youtube.daily_quota.toLocaleString()})
          </h3>
          {usage.youtube.entries.length === 0 ? (
            <p className="text-sm text-gray-400">오늘 기록된 호출이 없습니다.</p>
          ) : (
            <ul className="divide-y">
              {usage.youtube.entries.map((e) => (
                <li key={e.key_fp} className="py-1.5 flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <code className="font-mono text-xs text-gray-600">{e.key_fp}</code>
                    {e.is_system_key && (
                      <span className="text-xs text-amber-600 border border-amber-200 bg-amber-50 rounded px-1">시스템 키</span>
                    )}
                  </span>
                  <span
                    className={
                      e.pct >= 100
                        ? 'text-red-600 font-bold'
                        : e.pct >= 80
                          ? 'text-amber-600 font-bold'
                          : 'text-gray-600'
                    }
                  >
                    {e.units.toLocaleString()} 유닛 ({e.pct}%)
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="px-3 py-2">사용자</th><th className="px-3 py-2">모델</th>
              <th className="px-3 py-2">purpose</th><th className="px-3 py-2 text-right">호출</th>
              <th className="px-3 py-2 text-right">입력 토큰</th><th className="px-3 py-2 text-right">출력 토큰</th>
              <th className="px-3 py-2 text-right">비용</th>
            </tr>
          </thead>
          <tbody>
            {usage && usage.rows.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-4 text-center text-gray-400">사용 내역이 없습니다.</td></tr>
            )}
            {usage?.rows.map((r, idx) => (
              <tr key={idx} className="border-b last:border-0">
                <td className="px-3 py-2">{r.user_id == null ? '시스템' : (r.email ?? `user ${r.user_id}`)}</td>
                <td className="px-3 py-2">{r.model}</td>
                <td className="px-3 py-2">{r.purpose}</td>
                <td className="px-3 py-2 text-right">{r.calls.toLocaleString()}</td>
                <td className="px-3 py-2 text-right">{r.input_tokens.toLocaleString()}</td>
                <td className="px-3 py-2 text-right">{r.output_tokens.toLocaleString()}</td>
                <td className="px-3 py-2 text-right">{r.cost_usd == null ? '—' : `$${r.cost_usd.toFixed(4)}`}</td>
              </tr>
            ))}
          </tbody>
          {usage && (
            <tfoot>
              <tr className="border-t font-medium">
                <td className="px-3 py-2" colSpan={6}>총 비용</td>
                <td className="px-3 py-2 text-right">${usage.total_cost_usd.toFixed(4)}</td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  )
}
