import { useEffect, useRef, useState, useCallback } from 'react'
import { logApi } from '../api/logs'
import type { JobLog } from '../api/types'
import { useGroup } from '../group/useGroup'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import Pagination from '../components/Pagination'

const JOB_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '전체' },
  { value: 'channel_poll', label: '채널 모니터링' },
  { value: 'video_analyze', label: '영상 분석' },
  { value: 'notify', label: '텔레그램 알림' },
  { value: 'gateway_health', label: '게이트웨이 헬스' },
]

function formatJobTypeLabel(jobType: string): string {
  const map: Record<string, string> = {
    channel_poll: '채널 모니터링',
    video_analyze: '영상 분석',
    notify: '텔레그램 알림',
    gateway_health: '게이트웨이 헬스',
  }
  return map[jobType] ?? jobType
}

const STATUS_VALUES = ['', 'success', 'skip', 'fail']

function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    success: 'bg-green-100 text-green-800',
    fail: 'bg-red-100 text-red-800',
    skip: 'bg-yellow-100 text-yellow-800',
    running: 'bg-blue-100 text-blue-800',
  }
  const cls = variants[status.toLowerCase()] ?? 'bg-gray-100 text-gray-700'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {status}
    </span>
  )
}

function durationLabel(ms: number | null) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('ko-KR', {
    year: '2-digit', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export default function Jobs() {
  const { activeSlug } = useGroup()

  const [items, setItems] = useState<JobLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  const [jobType, setJobType] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchJobs = useCallback(async (p = page) => {
    setLoading(true)
    setError(null)
    try {
      const res = await logApi(activeSlug).listPaged({
        job_type: jobType || undefined,
        status: status || undefined,
        limit: PAGE_SIZE,
        offset: (p - 1) * PAGE_SIZE,
      })
      setItems(res.items)
      setTotal(res.total)
      setLastRefreshed(new Date())
    } catch (e: any) {
      setError(e.message ?? '로드 실패')
    } finally {
      setLoading(false)
    }
  }, [jobType, status, page, activeSlug])

  // 초기 및 필터 변경 시 fetch
  useEffect(() => {
    fetchJobs(1)
    setPage(1)
  }, [jobType, status])

  // 페이지 변경 시 fetch
  useEffect(() => {
    fetchJobs(page)
  }, [page])

  // 30초 자동 새로고침
  useEffect(() => {
    timerRef.current = setInterval(() => fetchJobs(page), 30_000)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [fetchJobs, page])

  // 요약 배지 계산
  const summary = {
    success: items.filter(i => i.status.toLowerCase() === 'success').length,
    fail: items.filter(i => i.status.toLowerCase() === 'fail').length,
    skip: items.filter(i => i.status.toLowerCase() === 'skip').length,
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Logs</h1>
          <p className="text-sm text-gray-500 mt-0.5">YouTube 모니터링 작업 실행 내역</p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-xs text-gray-400">
              마지막 갱신: {lastRefreshed.toLocaleTimeString('ko-KR')}
            </span>
          )}
          <button
            onClick={() => fetchJobs(page)}
            className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded-md hover:bg-gray-50 flex items-center gap-1"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            새로고침
          </button>
        </div>
      </div>

      {/* 요약 배지 */}
      <div className="flex gap-3 mb-4">
        <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 flex items-center gap-2 shadow-sm">
          <span className="text-xs text-gray-500 font-medium">현재 페이지</span>
          <span className="font-bold text-gray-900">{items.length}</span>
          <span className="text-xs text-gray-400">/ 전체 {total}</span>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-lg px-4 py-3 flex items-center gap-2 shadow-sm">
          <span className="text-xs text-green-700 font-medium">SUCCESS</span>
          <span className="font-bold text-green-800">{summary.success}</span>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 flex items-center gap-2 shadow-sm">
          <span className="text-xs text-red-700 font-medium">FAIL</span>
          <span className="font-bold text-red-800">{summary.fail}</span>
        </div>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 flex items-center gap-2 shadow-sm">
          <span className="text-xs text-yellow-700 font-medium">SKIP</span>
          <span className="font-bold text-yellow-800">{summary.skip}</span>
        </div>
      </div>

      {/* 필터 */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4 flex flex-wrap gap-4 items-end shadow-sm">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">잡 타입</label>
          <select
            value={jobType}
            onChange={e => setJobType(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-md text-sm"
          >
            {JOB_TYPE_OPTIONS.map((o) => (
              <option key={o.value || 'all'} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">상태</label>
          <select
            value={status}
            onChange={e => setStatus(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-md text-sm"
          >
            {STATUS_VALUES.map(s => (
              <option key={s} value={s}>{s || '전체'}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => { setJobType(''); setStatus('') }}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded-md hover:bg-gray-50"
        >
          초기화
        </button>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => fetchJobs(page)} />}

      {/* 로그 테이블 */}
      <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-600 w-36">잡 타입</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600 w-24">상태</th>
                <th className="px-4 py-3 text-left font-medium text-gray-600">메시지</th>
                <th className="px-4 py-3 text-right font-medium text-gray-600 w-24">소요시간</th>
                <th className="px-4 py-3 text-right font-medium text-gray-600 w-40">시작시간</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={5} className="py-12 text-center">
                    <Spinner />
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-10 text-center text-gray-400 text-sm">
                    로그가 없습니다.
                  </td>
                </tr>
              ) : (
                items.map(log => (
                  <tr key={log.log_pk} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-2.5 text-xs text-gray-700">{formatJobTypeLabel(log.job_type)}</td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={log.status} />
                    </td>
                    <td className="px-4 py-2.5 text-gray-700 max-w-xs">
                      <span className="line-clamp-2 break-all">{log.message ?? '-'}</span>
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-600 font-mono text-xs">
                      {durationLabel(log.duration_ms)}
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-500 text-xs whitespace-nowrap">
                      {formatDate(log.started_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 페이지네이션 */}
      {total > PAGE_SIZE && (
        <div className="mt-4">
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onChange={setPage}
          />
        </div>
      )}

      {/* 자동 새로고침 표시 */}
      <p className="mt-3 text-xs text-gray-400 text-right">30초마다 자동 새로고침</p>
    </div>
  )
}
