import { useEffect, useState } from 'react'
import { meApi, type MyUsageResponse } from '../api/me'

export default function MyPage() {
  const [data, setData] = useState<MyUsageResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    meApi.usage().then(setData).catch((e) => setError((e as Error).message))
  }, [])

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">마이페이지</h1>
        <a href="/" className="text-sm text-blue-600 hover:underline">← 앱으로</a>
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
      {data && (
        <section className="bg-white rounded-xl shadow-sm p-4 space-y-3">
          <h2 className="font-semibold text-gray-800">
            플랜: {data.plan_name}
            {data.unlimited && <span className="ml-2 text-xs text-gray-500">무제한</span>}
          </h2>
          <table className="w-full text-sm">
            <tbody>
              <tr><td className="py-1 text-gray-500">그룹</td>
                <td>{data.usage.group_count}{data.limits && ` / ${data.limits.max_groups}`}</td></tr>
              <tr><td className="py-1 text-gray-500">채널</td>
                <td>{data.usage.channel_count}{data.limits && ` / ${data.limits.max_channels_total}`}</td></tr>
              <tr><td className="py-1 text-gray-500">오늘 분석</td>
                <td>{data.usage.today_analyses}{data.limits && ` / ${data.limits.max_analyses_per_day}`}
                  <span className="text-xs text-gray-400 ml-1">(KST 자정 초기화)</span></td></tr>
              {data.limits && (<>
                <tr><td className="py-1 text-gray-500">영상 길이 한도</td>
                  <td>{data.limits.max_video_minutes}분</td></tr>
                <tr><td className="py-1 text-gray-500">폴링 주기 하한</td>
                  <td>{data.limits.min_poll_interval_min}분</td></tr>
              </>)}
            </tbody>
          </table>
        </section>
      )}
    </div>
  )
}
