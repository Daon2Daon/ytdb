import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import type { Digest } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function DigestDetail() {
  const { activeSlug } = useGroup()
  const { digestPk } = useParams<{ digestPk: string }>()
  const [digest, setDigest] = useState<Digest | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    if (!digestPk) return
    setLoading(true)
    setError(null)
    try {
      setDigest(await digestApi(activeSlug).get(Number(digestPk)))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug, digestPk])

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!digest) return null

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Link to={`/g/${activeSlug}/digests`} className="hover:text-blue-600">주간 리뷰</Link>
        <span>/</span>
        <span className="text-gray-700 truncate">{digest.headline || '주간 리뷰'}</span>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
        <h1 className="text-xl font-bold text-gray-900">{digest.headline || '주간 리뷰'}</h1>
        <p className="text-sm text-gray-500">
          {dayjs(digest.period_start).format('YYYY-MM-DD')} ~ {dayjs(digest.period_end).format('YYYY-MM-DD')}
          {' · '}분석 영상 {digest.video_count}건 · 상태 {digest.status}
        </p>
        {digest.error && <p className="text-sm text-red-600">{digest.error}</p>}
      </div>

      {digest.summary_md && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">요약</h2>
          <article className="prose prose-sm max-w-none text-gray-700 break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{digest.summary_md}</ReactMarkdown>
          </article>
        </div>
      )}

      {digest.top_tags && digest.top_tags.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">상위 태그</h2>
          <div className="flex flex-wrap gap-2">
            {digest.top_tags.map((t) => (
              <span key={t.name} className="px-2.5 py-0.5 bg-gray-100 text-gray-600 rounded-full text-xs">
                {t.name} ({t.count})
              </span>
            ))}
          </div>
        </div>
      )}

      {digest.top_channels && digest.top_channels.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">상위 채널</h2>
          <ul className="space-y-1 text-sm text-gray-700">
            {digest.top_channels.map((c) => <li key={c.name}>{c.name} ({c.count})</li>)}
          </ul>
        </div>
      )}

      {digest.sentiment_breakdown && Object.keys(digest.sentiment_breakdown).length > 0 && (
        <div className="bg-white rounded-xl shadow-sm p-5">
          <h2 className="font-semibold text-gray-800 mb-3">감성 분포</h2>
          <div className="flex flex-wrap gap-3 text-sm text-gray-700">
            {Object.entries(digest.sentiment_breakdown).map(([k, v]) => (
              <span key={k}>{k}: {v}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
