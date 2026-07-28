import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { commentApi, creditsApi } from '../api/comments'
import type { CommentAnalysisListItem, CommentCredits } from '../api/types'
import { useGroup } from '../group/useGroup'
import { LIMIT_OPTIONS, creditCost, optionEnabled } from '../components/CommentAnalysis.logic'

export default function CommentAnalysis() {
  const { activeSlug } = useGroup()
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [limit, setLimit] = useState(1000)
  const [credits, setCredits] = useState<CommentCredits | null>(null)
  const [recent, setRecent] = useState<CommentAnalysisListItem[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    creditsApi.get().then(setCredits).catch(() => {})
    commentApi(activeSlug).list().then(setRecent).catch(() => {})
  }, [activeSlug])

  const state = credits
    ? { unlimited: credits.unlimited, perAnalysisMax: credits.per_analysis_max }
    : { unlimited: false, perAnalysisMax: 1000 }
  const cost = creditCost(limit)
  const remaining = credits ? Math.max(0, credits.limit - credits.used) : 0

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return
    setBusy(true)
    setErr(null)
    try {
      const res = await commentApi(activeSlug).startByUrl(url.trim(), limit)
      navigate(`/g/${activeSlug}/videos/${res.video_pk}`)
    } catch (e2) {
      setErr((e2 as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">댓글 반응</h1>
        <p className="mt-1 text-sm text-gray-500">
          YouTube URL을 입력하면 댓글을 긍정·부정·중립으로 분류하고 인사이트를 뽑습니다.
        </p>
      </div>

      {credits && !credits.unlimited && (
        <div className="bg-white rounded-xl shadow-sm p-4 text-sm text-gray-700">
          이번 달 <b>{credits.used}</b> / {credits.limit}회 사용 · 잔여 <b>{remaining}</b>회
          <span className="block text-xs text-gray-400 mt-1">KST 월초에 초기화됩니다.</span>
        </div>
      )}

      <form onSubmit={submit} className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        {err && <p className="text-sm text-red-600">{err}</p>}
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=..."
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            className="border border-gray-300 rounded-lg px-2 py-2 text-sm"
          >
            {LIMIT_OPTIONS.map((n) => (
              <option key={n} value={n} disabled={!optionEnabled(n, state)}>
                {n.toLocaleString()}건{optionEnabled(n, state) ? '' : ' (플랜 상한 초과)'}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || !url.trim() || !optionEnabled(limit, state)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
          >
            {busy ? '시작 중…' : '분석하기'}
          </button>
          {credits && !credits.unlimited && (
            <span className="text-xs text-gray-500">
              {cost}회 차감 · 잔여 {remaining} → {Math.max(0, remaining - cost)}회
            </span>
          )}
        </div>
      </form>

      {recent.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-6 space-y-2">
          <h2 className="font-semibold text-gray-800">최근 분석</h2>
          {recent.map((r) => (
            <Link
              key={r.video_pk}
              to={`/g/${activeSlug}/videos/${r.video_pk}/comments`}
              className="flex items-center gap-3 py-2 hover:bg-gray-50 rounded-lg px-2"
            >
              {r.thumbnail_url && (
                <img src={r.thumbnail_url} alt="" className="w-16 h-9 object-cover rounded" />
              )}
              <span className="flex-1 min-w-0 text-sm text-gray-800 truncate">{r.title}</span>
              <span className="text-xs text-gray-500 whitespace-nowrap">
                {r.status === 'done'
                  ? `긍 ${r.positive_count ?? 0} · 부 ${r.negative_count ?? 0}`
                  : r.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
