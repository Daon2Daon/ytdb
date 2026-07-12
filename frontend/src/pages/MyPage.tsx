import { useEffect, useRef, useState } from 'react'
import { meApi, type MyUsageResponse, type TelegramDestination } from '../api/me'

const POLL_INTERVAL_MS = 3000
const POLL_MAX_ATTEMPTS = 40

export default function MyPage() {
  const [data, setData] = useState<MyUsageResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [destinations, setDestinations] = useState<TelegramDestination[]>([])
  const [tgError, setTgError] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    meApi.usage().then(setData).catch((e) => setError((e as Error).message))
    loadDestinations()
    return () => stopPolling()
  }, [])

  function loadDestinations() {
    return meApi.telegramDestinations().then(setDestinations).catch((e) => setTgError((e as Error).message))
  }

  function stopPolling() {
    if (pollRef.current != null) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    setLinking(false)
  }

  async function handleLinkClick() {
    if (linking) return
    setTgError(null)
    setLinking(true)
    try {
      const resp = await meApi.telegramLinkToken()
      window.open(resp.deep_link, '_blank')
      const baseCount = destinations.length
      let attempts = 0
      pollRef.current = setInterval(async () => {
        attempts += 1
        try {
          const list = await meApi.telegramDestinations()
          if (list.length > baseCount) {
            setDestinations(list)
            stopPolling()
            return
          }
        } catch {
          // 폴링 중 일시 오류는 무시하고 계속 시도
        }
        if (attempts >= POLL_MAX_ATTEMPTS) {
          stopPolling()
        }
      }, POLL_INTERVAL_MS)
    } catch (e) {
      stopPolling()
      setTgError((e as Error).message)
    }
  }

  async function handleUnlink(destId: number) {
    try {
      await meApi.deleteTelegramDestination(destId)
      await loadDestinations()
    } catch (e) {
      setTgError((e as Error).message)
    }
  }

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
              <tr><td className="py-1 text-gray-500">당월 AI 비용</td>
                <td>${data.usage.month_cost_usd.toFixed(4)}
                  {data.limits?.monthly_cost_budget_usd != null && ` / $${data.limits.monthly_cost_budget_usd}`}
                  <span className="text-xs text-gray-400 ml-1">(KST 월초 초기화)</span></td></tr>
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

      <section className="bg-white rounded-xl shadow-sm p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">텔레그램 연결</h2>
          <button
            onClick={handleLinkClick}
            disabled={linking}
            className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            연결하기
          </button>
        </div>
        {tgError && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{tgError}</p>}
        {linking && (
          <p className="text-xs text-gray-500">텔레그램에서 '시작'을 누르면 자동으로 연결됩니다…</p>
        )}
        {destinations.length === 0 ? (
          <p className="text-sm text-gray-500">연결된 텔레그램이 없습니다. 연결하면 분석 알림을 받을 수 있습니다.</p>
        ) : (
          <ul className="divide-y">
            {destinations.map((d) => (
              <li key={d.dest_id} className="py-2 flex items-center justify-between text-sm">
                <span>
                  {d.title ?? '연결됨'}
                  <span className="text-xs text-gray-400 ml-2">
                    {new Date(d.linked_at).toLocaleDateString('ko-KR')} 연결
                  </span>
                </span>
                <button onClick={() => handleUnlink(d.dest_id)} className="text-red-600 text-xs hover:underline">
                  해제
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
