import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { videoApi } from '../api/videos'
import { channelApi } from '../api/channels'
import { tagApi } from '../api/tags'
import type { Video, Channel, Tag } from '../api/types'
import { useGroup } from '../group/useGroup'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import StatusBadge from '../components/StatusBadge'
import NotifyBadge from '../components/NotifyBadge'
import Pagination from '../components/Pagination'

function formatDuration(sec: number | null) {
  if (!sec) return ''
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

export default function Videos() {
  const { activeSlug } = useGroup()
  const [searchParams, setSearchParams] = useSearchParams()
  const [videos, setVideos] = useState<Video[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [channels, setChannels] = useState<Channel[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [deleteTarget, setDeleteTarget] = useState<Video | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [resetting, setResetting] = useState(false)

  const page = Number(searchParams.get('page') ?? 1)
  const channelPk = searchParams.get('channel_pk') ? Number(searchParams.get('channel_pk')) : undefined
  const tagFilter = searchParams.get('tag') ?? undefined
  const statusFilter = searchParams.get('status') ?? undefined
  const notifiedFilter = searchParams.get('notified') ?? undefined
  const PAGE_SIZE = 20

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [r, chs, tgs] = await Promise.all([
        videoApi(activeSlug).listPaged({ channel_pk: channelPk, tag: tagFilter, status: statusFilter, notified: notifiedFilter, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
        channelApi(activeSlug).list(),
        tagApi(activeSlug).list(2, 50),
      ])
      setVideos(r.items)
      setTotal(r.total)
      setChannels(chs)
      setTags(tgs)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, channelPk, tagFilter, statusFilter, notifiedFilter, activeSlug])

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await videoApi(activeSlug).remove(deleteTarget.video_pk)
      setDeleteTarget(null)
      const remainingOnPage = videos.length - 1
      if (remainingOnPage === 0 && page > 1) {
        const next = new URLSearchParams(searchParams)
        next.set('page', String(page - 1))
        setSearchParams(next)
      } else {
        await load()
      }
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const handleResetFailed = async () => {
    if (!window.confirm('실패한 영상을 모두 분석 대기열로 되돌립니다(재시도 횟수 초기화). 계속할까요?')) return
    setResetting(true)
    try {
      const r = await videoApi(activeSlug).resetFailed()
      alert(`${r.reset}건을 대기열로 되돌렸습니다.`)
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setResetting(false)
    }
  }

  const setFilter = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    next.set('page', '1')
    setSearchParams(next)
  }

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-gray-900">영상 목록</h1>

      {/* 필터 바 */}
      <div className="bg-white rounded-xl shadow-sm p-4 flex flex-wrap gap-3">
        <select
          value={channelPk ?? ''}
          onChange={(e) => setFilter('channel_pk', e.target.value || undefined)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">전체 채널</option>
          {channels.map((c) => <option key={c.channel_pk} value={c.channel_pk}>{c.channel_name}</option>)}
        </select>

        <select
          value={statusFilter ?? ''}
          onChange={(e) => setFilter('status', e.target.value || undefined)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">전체 상태</option>
          <option value="pending">대기</option>
          <option value="processing">분석 중</option>
          <option value="done">완료</option>
          <option value="failed">실패</option>
        </select>

        <select
          value={tagFilter ?? ''}
          onChange={(e) => setFilter('tag', e.target.value || undefined)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">전체 태그</option>
          {tags.map((t) => <option key={t.tag_pk} value={t.name}>{t.name} ({t.video_count})</option>)}
        </select>

        <select
          value={notifiedFilter ?? ''}
          onChange={(e) => setFilter('notified', e.target.value || undefined)}
          className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">전체 발송</option>
          <option value="yes">발송 완료</option>
          <option value="no">미발송</option>
        </select>

        {(channelPk || tagFilter || statusFilter || notifiedFilter) && (
          <button
            onClick={() => setSearchParams({ page: '1' })}
            className="text-sm text-gray-500 hover:text-red-500 underline"
          >
            필터 초기화
          </button>
        )}

        <button
          onClick={handleResetFailed}
          disabled={resetting}
          className="text-xs px-2.5 py-1.5 rounded-lg bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100 disabled:opacity-60"
          title="실패(failed) 영상을 전부 pending으로 되돌리고 재시도 횟수를 초기화합니다"
        >
          {resetting ? '되돌리는 중...' : '실패 영상 일괄 재분석'}
        </button>

        <span className="ml-auto text-sm text-gray-400 self-center">총 {total}개</span>
      </div>

      {/* 영상 목록 */}
      {videos.length === 0 ? (
        <div className="bg-white rounded-xl py-16 text-center text-gray-400 shadow-sm">
          <p className="text-5xl mb-3">🎬</p>
          <p>조건에 맞는 영상이 없습니다.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {videos.map((v) => (
            <div
              key={v.video_pk}
              className="bg-white rounded-xl shadow-sm hover:shadow-md transition-shadow flex gap-4 p-3"
            >
              <Link to={`/g/${activeSlug}/videos/${v.video_pk}`} className="flex gap-4 flex-1 min-w-0">
                <div className="relative shrink-0">
                  {v.thumbnail_url ? (
                    <img src={v.thumbnail_url} alt={v.title} className="w-40 aspect-video rounded-lg object-cover" />
                  ) : (
                    <div className="w-40 aspect-video rounded-lg bg-gray-100 flex items-center justify-center text-2xl">🎬</div>
                  )}
                  {v.duration_seconds && (
                    <span className="absolute bottom-1 right-1 bg-black/70 text-white text-xs px-1.5 py-0.5 rounded">
                      {formatDuration(v.duration_seconds)}
                    </span>
                  )}
                </div>
                <div className="flex-1 min-w-0 py-1 space-y-1.5">
                  <p className="font-medium text-gray-900 line-clamp-2 text-sm leading-snug">{v.title}</p>
                  {v.summary?.one_line && (
                    <p className="text-xs text-gray-500 line-clamp-1">{v.summary.one_line}</p>
                  )}
                  <div className="flex items-center gap-2 flex-wrap">
                    <StatusBadge status={v.analysis_status} />
                    <NotifyBadge analysisStatus={v.analysis_status} notifiedAt={v.notified_at} />
                    {v.source_channel_name && (
                      <span className="text-xs text-purple-600 bg-purple-50 border border-purple-200 px-2 py-0.5 rounded-full">
                        추가 · {v.source_channel_name}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-400">
                    <span>📅 {dayjs(v.published_at).format('YYYY-MM-DD HH:mm')}</span>
                    {v.view_count != null && <span>👁 {v.view_count.toLocaleString()}</span>}
                  </div>
                </div>
              </Link>
              <div className="flex flex-col justify-center shrink-0 pr-1">
                <button
                  type="button"
                  onClick={() => setDeleteTarget(v)}
                  className="px-2.5 py-1.5 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100"
                >
                  삭제
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={(p) => {
        const next = new URLSearchParams(searchParams)
        next.set('page', String(p))
        setSearchParams(next)
      }} />

      {deleteTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full space-y-4">
            <h3 className="font-bold text-gray-900">영상 삭제 확인</h3>
            <p className="text-sm text-gray-600">
              아래 영상과 연관된 분석·태그 데이터가 삭제됩니다. 계속하시겠습니까?
            </p>
            <p className="text-sm font-medium text-gray-800 line-clamp-3">{deleteTarget.title}</p>
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? '삭제 중...' : '삭제'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
