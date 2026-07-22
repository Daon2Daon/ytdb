import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import type { Digest, DigestSection } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export function toRenderSections(digest: Digest): DigestSection[] {
  const secs = digest.digest_sections
  if (Array.isArray(secs) && secs.length) return secs
  if (digest.summary_md && digest.summary_md.trim()) {
    return [{ key: '_legacy', kind: 'llm', title: '요약', body_md: digest.summary_md }]
  }
  return []
}

export function computedToMarkdown(s: DigestSection): string {
  const items = (s.data?.items as { name?: string; count?: number; channel?: string; head?: string; views?: number }[]) ?? []
  if (s.key === 'top_tags' || s.key === 'top_channels') {
    return items.map((it) => `- ${it.name} (${it.count})`).join('\n')
  }
  if (s.key === 'top_viewed') {
    return items.map((it) => `- [${it.channel}] ${it.head}`).join('\n')
  }
  const breakdown = (s.data?.breakdown as Record<string, number>) ?? {}
  if (s.key === 'sentiment_breakdown') {
    return Object.entries(breakdown).map(([k, v]) => `- ${k}: ${v}`).join('\n')
  }
  if (s.key === 'stats_overview') {
    return `- 분석 영상 ${(s.data?.video_count as number) ?? 0}건`
  }
  if (s.key === 'entity_pivot') {
    const pitems = (s.data?.items as {
      entity?: string; count?: number; samples?: string[]; by?: Record<string, number>
    }[]) ?? []
    return pitems.map((it) => {
      let suffix = it.samples?.length ? ` — ${it.samples.join(' / ')}` : ''
      const byKeys = Object.keys(it.by ?? {})
      if (byKeys.length) suffix += ` (${byKeys.map((k) => `${k} ${it.by![k]}`).join(', ')})`
      return `- **${it.entity}** ${it.count}건${suffix}`
    }).join('\n')
  }
  if (s.key === 'period_compare') {
    const d = s.data as {
      new?: { entity: string }[]; gone?: { entity: string }[]
      continuing?: { entity: string; cur: number; prev: number }[]
    } | undefined
    const lines: string[] = []
    if (d?.new?.length) lines.push(`- 신규: ${d.new.map((x) => x.entity).join(', ')}`)
    if (d?.gone?.length) lines.push(`- 소멸: ${d.gone.map((x) => x.entity).join(', ')}`)
    d?.continuing?.forEach((x) => lines.push(`- 지속: ${x.entity} (${x.prev}→${x.cur}건)`))
    return lines.join('\n')
  }
  if (s.key === 'top_records') {
    const ritems = (s.data?.items as {
      entity?: string | null; value?: number; text?: string | null; date?: string | null
    }[]) ?? []
    return ritems
      .map((it) => `- ${it.entity ?? it.text ?? ''}: ${it.value}${it.date ? ` · ${it.date}` : ''}`)
      .join('\n')
  }
  return ''
}

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
        <Link to={`/g/${activeSlug}/digests`} className="hover:text-blue-600">다이제스트</Link>
        <span>/</span>
        <span className="text-gray-700 truncate">{digest.headline || '다이제스트'}</span>
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
        <h1 className="text-xl font-bold text-gray-900">{digest.headline || '다이제스트'}</h1>
        <p className="text-sm text-gray-500">
          {dayjs(digest.period_start).format('YYYY-MM-DD')} ~ {dayjs(digest.period_end).format('YYYY-MM-DD')}
          {' · '}분석 영상 {digest.video_count}건 · 상태 {digest.status}
        </p>
        {digest.error && <p className="text-sm text-red-600">{digest.error}</p>}
      </div>

      {toRenderSections(digest).map((s) => {
        const computed = s.kind !== 'llm' ? computedToMarkdown(s) : ''
        const body = s.kind === 'llm' ? (s.body_md ?? '')
          : s.kind === 'hybrid'
            ? [s.body_md ?? '', computed].filter((x) => x.trim()).join('\n\n')
            : computed
        if (!body.trim()) return null
        return (
          <div key={s.key} className="bg-white rounded-xl shadow-sm p-5">
            {s.title && <h2 className="font-semibold text-gray-800 mb-3">{s.title}</h2>}
            <article className="prose prose-sm max-w-none text-gray-700 break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
            </article>
          </div>
        )
      })}

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
