import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { commentApi } from '../api/comments'
import type { CommentAnalysisOut, CommentCategory } from '../api/types'
import { useGroup } from '../group/useGroup'
import { ratioPercents } from '../components/CommentAnalysis.logic'

const TABS: { key: 'positive' | 'negative' | 'neutral'; label: string; color: string }[] = [
  { key: 'positive', label: '긍정', color: 'bg-emerald-500' },
  { key: 'negative', label: '부정', color: 'bg-rose-500' },
  { key: 'neutral', label: '중립', color: 'bg-gray-400' },
]

function CategoryPanel({ data }: { data: CommentCategory }) {
  return (
    <div className="space-y-4">
      {data.summary && <p className="text-sm text-gray-700">{data.summary}</p>}
      {data.key_points.length > 0 && (
        <ul className="list-disc pl-5 space-y-1 text-sm text-gray-700">
          {data.key_points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
      {data.insights && (
        <div className="bg-blue-50 rounded-lg p-3 text-sm text-gray-700">{data.insights}</div>
      )}
      <div className="space-y-2">
        {data.top_comments.map((c, i) => (
          <div key={i} className="border border-gray-200 rounded-lg p-3">
            <div className="flex justify-between text-xs text-gray-500">
              <span>{c.author}</span>
              <span>👍 {c.like_count}</span>
            </div>
            <p className="mt-1 text-sm text-gray-800 whitespace-pre-wrap">{c.text}</p>
          </div>
        ))}
        {data.top_comments.length === 0 && (
          <p className="text-sm text-gray-400">해당 카테고리의 댓글이 없습니다.</p>
        )}
      </div>
    </div>
  )
}

export default function VideoComments() {
  const { activeSlug } = useGroup()
  const { videoPk } = useParams()
  const pk = Number(videoPk)
  const [data, setData] = useState<CommentAnalysisOut | null>(null)
  const [tab, setTab] = useState<'positive' | 'negative' | 'neutral'>('positive')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    commentApi(activeSlug)
      .get(pk)
      .then(setData)
      .catch((e) => setErr((e as Error).message))
  }, [activeSlug, pk])

  if (err) return <p className="text-sm text-red-600">{err}</p>
  if (!data) return <p className="text-sm text-gray-500">불러오는 중…</p>
  if (data.status !== 'done' || !data.result)
    return <p className="text-sm text-gray-500">아직 분석 결과가 없습니다.</p>

  const pct = ratioPercents(data.positive_count, data.negative_count, data.neutral_count)
  const counts = {
    positive: data.positive_count ?? 0,
    negative: data.negative_count ?? 0,
    neutral: data.neutral_count ?? 0,
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">댓글 반응</h1>
        <Link to={`/g/${activeSlug}/videos/${pk}`} className="text-sm text-blue-600 hover:underline">
          ← 영상 상세
        </Link>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-6 space-y-3">
        <div className="flex h-3 rounded-full overflow-hidden bg-gray-100">
          <div className="bg-emerald-500" style={{ width: `${pct.positive}%` }} />
          <div className="bg-rose-500" style={{ width: `${pct.negative}%` }} />
          <div className="bg-gray-400" style={{ width: `${pct.neutral}%` }} />
        </div>
        <p className="text-xs text-gray-500">
          전체 {(data.total_count ?? data.fetched_count ?? 0).toLocaleString()}건 중 상위{' '}
          {(data.fetched_count ?? 0).toLocaleString()}건 분석
          {data.model ? ` · ${data.model}` : ''}
        </p>
        {data.partial && (
          <p className="text-xs text-amber-600">일부 댓글은 분류에 실패해 중립으로 처리했습니다.</p>
        )}
      </div>

      <div className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex gap-2 border-b border-gray-200">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 py-2 text-sm border-b-2 -mb-px ${
                tab === t.key
                  ? 'border-blue-600 text-blue-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label} {counts[t.key]}
            </button>
          ))}
        </div>
        <CategoryPanel data={data.result.categories[tab]} />
      </div>
    </div>
  )
}
