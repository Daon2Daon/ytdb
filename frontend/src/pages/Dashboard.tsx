import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { statsApi } from '../api/stats'
import { healthApi } from '../api/health'
import { videoApi } from '../api/videos'
import type { Stats, DBHealthResponse, GatewayHealthResponse, Video } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import StatusBadge from '../components/StatusBadge'
import OnboardingChecklist from '../components/OnboardingChecklist'

function StatCard({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-5 flex flex-col gap-1">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-3xl font-bold ${color ?? 'text-gray-900'}`}>{value}</span>
    </div>
  )
}

export default function Dashboard() {
  const { activeSlug } = useGroup()
  const [stats, setStats] = useState<Stats | null>(null)
  const [db, setDb] = useState<DBHealthResponse | null>(null)
  const [gw, setGw] = useState<GatewayHealthResponse | null>(null)
  const [recent, setRecent] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, h, g, v] = await Promise.allSettled([
        statsApi(activeSlug).get(),
        healthApi(activeSlug).db(),
        healthApi(activeSlug).gateway(),
        videoApi(activeSlug).listPaged({ limit: 12, offset: 0 }),
      ])
      if (s.status === 'fulfilled') setStats(s.value)
      if (h.status === 'fulfilled') setDb(h.value)
      if (g.status === 'fulfilled') setGw(g.value)
      if (v.status === 'fulfilled') setRecent(v.value.items)
      if (s.status === 'rejected') setError((s.reason as Error).message)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  if (loading) return <Spinner />

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">대시보드</h1>

      <OnboardingChecklist />

      {error && <ErrorBanner message={error} onRetry={load} />}

      <div className="space-y-2">
        {db && !db.healthy && (
          <div className="rounded-lg bg-red-50 border border-red-300 px-4 py-3 text-red-700 text-sm flex items-center gap-2">
            <span className="font-semibold">DB 오류</span>
            <span>{db.message}</span>
          </div>
        )}
        {db?.healthy && (
          <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-green-700 text-sm">
            DB 정상 {db.latency_ms != null && `· 응답 ${db.latency_ms}ms`}
          </div>
        )}
        {gw && !gw.success && (
          <div className="rounded-lg bg-orange-50 border border-orange-300 px-4 py-3 text-orange-700 text-sm">
            <span className="font-semibold">AI Gateway 오류</span> {gw.message}
          </div>
        )}
        {gw?.success && (
          <div className="rounded-lg bg-green-50 border border-green-200 px-4 py-3 text-green-700 text-sm">
            AI Gateway 정상 · {gw.message}
          </div>
        )}
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="전체 채널" value={stats.total_channels} />
          <StatCard label="활성 채널" value={stats.active_channels} color="text-blue-600" />
          <StatCard label="전체 영상" value={stats.total_videos} />
          <StatCard label="분석 완료" value={stats.analyzed_videos} color="text-green-600" />
          <StatCard label="분석 대기" value={stats.pending_videos} color="text-yellow-600" />
          <StatCard label="분석 실패" value={stats.failed_videos} color="text-red-600" />
          <StatCard label="알림 발송" value={stats.notified_videos} />
          <StatCard label="전체 태그" value={stats.total_tags} />
        </div>
      )}

      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-800">최신 영상</h2>
          <Link to={`/g/${activeSlug}/videos`} className="text-blue-600 text-sm hover:underline">전체 보기 →</Link>
        </div>
        {recent.length === 0 ? (
          <p className="text-gray-500 text-sm text-center py-8">표시할 영상이 없습니다.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {recent.map((v) => (
              <Link
                key={v.video_pk}
                to={`/g/${activeSlug}/videos/${v.video_pk}`}
                className="bg-white rounded-xl shadow-sm overflow-hidden hover:shadow-md hover:ring-2 hover:ring-blue-200 transition-all"
              >
                {v.thumbnail_url ? (
                  <img src={v.thumbnail_url} alt={v.title} className="w-full aspect-video object-cover" />
                ) : (
                  <div className="w-full aspect-video bg-gray-100 flex items-center justify-center text-gray-400 text-4xl">🎬</div>
                )}
                <div className="p-3 space-y-1.5">
                  <p className="text-sm font-medium text-gray-900 line-clamp-2">{v.title}</p>
                  {v.summary?.one_line && <p className="text-xs text-gray-500 line-clamp-1">{v.summary.one_line}</p>}
                  <div className="flex items-center justify-between gap-2">
                    <StatusBadge status={v.analysis_status} />
                    <span className="text-xs text-gray-400">{dayjs(v.published_at).format('MM/DD HH:mm')}</span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
