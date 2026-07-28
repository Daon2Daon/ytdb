import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { commentApi, creditsApi } from '../api/comments'
import type { CommentAnalysisOut, CommentCredits } from '../api/types'
import {
  LIMIT_OPTIONS,
  cardState,
  creditCost,
  freshness,
  optionEnabled,
  ratioPercents,
} from './CommentAnalysis.logic'

type Props = { slug: string; videoPk: number }

export default function CommentAnalysisCard({ slug, videoPk }: Props) {
  const [data, setData] = useState<CommentAnalysisOut | null>(null)
  const [credits, setCredits] = useState<CommentCredits | null>(null)
  const [limit, setLimit] = useState(1000)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const load = async () => {
    try {
      setData(await commentApi(slug).get(videoPk))
    } catch {
      setData(null) // 404 = 아직 분석 없음
    }
  }

  useEffect(() => {
    load()
    creditsApi.get().then(setCredits).catch(() => {})
    return () => stopPoll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug, videoPk])

  // 실행 중이면 3초 폴링, 10분 타임아웃(상한 5,000건이 2~4분 걸린다).
  useEffect(() => {
    if (data?.status !== 'running' && data?.status !== 'pending') {
      stopPoll()
      return
    }
    if (pollRef.current) return
    pollRef.current = setInterval(load, 3000)
    const timer = setTimeout(stopPoll, 10 * 60 * 1000)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.status])

  const start = async () => {
    setBusy(true)
    setErr(null)
    try {
      await commentApi(slug).start(videoPk, limit)
      await load()
      creditsApi.get().then(setCredits).catch(() => {})
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const state = credits
    ? { unlimited: credits.unlimited, perAnalysisMax: credits.per_analysis_max }
    : { unlimited: false, perAnalysisMax: 1000 }
  const cost = creditCost(limit)
  const remaining = credits ? Math.max(0, credits.limit - credits.used) : 0
  const fresh = freshness(data?.current_comment_count, data?.total_count)
  const st = cardState(data?.status, data?.current_comment_count, data?.total_count)
  const pct = ratioPercents(
    data?.positive_count ?? null,
    data?.negative_count ?? null,
    data?.neutral_count ?? null,
  )

  const running = st === 'running'
  const analyzed = st === 'done' || st === 'stale'

  return (
    <div className="bg-white rounded-xl shadow-sm p-6 space-y-3">
      <h2 className="font-semibold text-gray-800">💬 댓글 반응</h2>
      {err && <p className="text-sm text-red-600">{err}</p>}

      {running && (
        <p className="text-sm text-gray-500">분석 중입니다… 완료되면 자동으로 갱신됩니다.</p>
      )}

      {st === 'failed' && (
        <div className="text-sm text-red-600 space-y-1">
          <p>{data?.error || '분석에 실패했습니다.'}</p>
          <p className="text-xs text-gray-500">크레딧은 차감되지 않았습니다.</p>
        </div>
      )}

      {analyzed && data && (
        <div className="space-y-2">
          <div className="flex h-3 rounded-full overflow-hidden bg-gray-100">
            <div className="bg-emerald-500" style={{ width: `${pct.positive}%` }} />
            <div className="bg-rose-500" style={{ width: `${pct.negative}%` }} />
            <div className="bg-gray-400" style={{ width: `${pct.neutral}%` }} />
          </div>
          <div className="flex gap-3 text-xs text-gray-600">
            <span>긍정 {data.positive_count ?? 0}건 ({pct.positive}%)</span>
            <span>부정 {data.negative_count ?? 0}건 ({pct.negative}%)</span>
            <span>중립 {data.neutral_count ?? 0}건 ({pct.neutral}%)</span>
          </div>
          {data.partial && (
            <p className="text-xs text-amber-600">일부 댓글은 분류에 실패해 중립으로 처리했습니다.</p>
          )}
          {fresh.stale && (
            <p className="text-xs text-amber-600">
              분석 이후 댓글 {fresh.added.toLocaleString()}건이 추가됐습니다. 다시 분석할 수 있습니다.
            </p>
          )}
          <Link
            to={`/g/${slug}/videos/${videoPk}/comments`}
            className="inline-block text-sm text-blue-600 hover:underline"
          >
            자세히 보기 →
          </Link>
        </div>
      )}

      {!running && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
          >
            {LIMIT_OPTIONS.map((n) => (
              <option key={n} value={n} disabled={!optionEnabled(n, state)}>
                {n.toLocaleString()}건{optionEnabled(n, state) ? '' : ' (플랜 상한 초과)'}
              </option>
            ))}
          </select>
          <button
            onClick={start}
            disabled={busy || !optionEnabled(limit, state)}
            className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60"
          >
            {analyzed ? '다시 분석' : '분석하기'}
          </button>
          {credits && !credits.unlimited && (
            <span className="text-xs text-gray-500">
              {cost}회 차감 · 잔여 {remaining} → {Math.max(0, remaining - cost)}회
            </span>
          )}
        </div>
      )}
    </div>
  )
}
