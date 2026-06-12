import { useEffect, useRef, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import dayjs from 'dayjs'
import { videoApi } from '../api/videos'
import { promptApi } from '../api/prompts'
import type { VideoDetail as VideoDetailType } from '../api/types'
import { useGroup } from '../group/useGroup'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import StatusBadge from '../components/StatusBadge'
import ConfirmModal from '../components/ConfirmModal'

function ConfidenceBar({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const color = score >= 0.7 ? 'bg-green-500' : score >= 0.4 ? 'bg-yellow-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-500 w-8 text-right">{pct}%</span>
    </div>
  )
}

export default function VideoDetail() {
  const { activeSlug } = useGroup()
  const { videoPk } = useParams<{ videoPk: string }>()
  const navigate = useNavigate()
  const [video, setVideo] = useState<VideoDetailType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [notifying, setNotifying] = useState(false)
  const [promptOpen, setPromptOpen] = useState(false)
  const [customPrompt, setCustomPrompt] = useState('')
  const [promptLoaded, setPromptLoaded] = useState(false)
  const [previewHtml, setPreviewHtml] = useState<string | null>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current)
      pollingTimeoutRef.current = null
    }
  }

  const load = async () => {
    if (!videoPk) return
    setLoading(true)
    setError(null)
    try {
      setVideo(await videoApi(activeSlug).get(Number(videoPk)))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const silentRefresh = async () => {
    if (!videoPk) return
    try {
      const updated = await videoApi(activeSlug).get(Number(videoPk))
      setVideo(updated)
      if (updated.analysis_status !== 'processing' && updated.analysis_status !== 'pending') {
        stopPolling()
        setReanalyzing(false)
      }
    } catch {
      stopPolling()
      setReanalyzing(false)
    }
  }

  useEffect(() => { load() }, [videoPk])

  useEffect(() => stopPolling, [])

  // 분석 완료 시, 실제 발송 본문(그룹 템플릿 기준)을 백엔드에서 그대로 받아 미리본다.
  // 재분석·발송으로 상태가 바뀌면 다시 받아 미리보기와 실발송을 일치시킨다.
  useEffect(() => {
    if (!videoPk || video?.analysis_status !== 'done') {
      setPreviewHtml(null)
      return
    }
    let cancelled = false
    videoApi(activeSlug)
      .notifyPreview(Number(videoPk))
      .then((r) => { if (!cancelled) setPreviewHtml(r.text || null) })
      .catch(() => { if (!cancelled) setPreviewHtml(null) })
    return () => { cancelled = true }
  }, [videoPk, activeSlug, video?.analysis_status, video?.notified_at])

  const handleDelete = async () => {
    if (!videoPk) return
    setDeleting(true)
    stopPolling()
    try {
      await videoApi(activeSlug).remove(Number(videoPk))
      navigate(`/g/${activeSlug}/videos`)
    } catch (e) {
      alert((e as Error).message)
      setDeleting(false)
      setDeleteConfirm(false)
    }
  }

  const handleReanalyze = async () => {
    if (!videoPk) return
    setReanalyzing(true)
    try {
      await videoApi(activeSlug).analyzeNow(Number(videoPk), promptOpen && customPrompt.trim() ? customPrompt.trim() : undefined)
      await silentRefresh()
      pollingRef.current = setInterval(silentRefresh, 2000)
      // 3분 후 주기적 새로고침 자동 중단 (분석이 비정상적으로 오래 걸리는 경우 대비)
      pollingTimeoutRef.current = setTimeout(() => {
        stopPolling()
        setReanalyzing(false)
      }, 3 * 60 * 1000)
    } catch (e) {
      setReanalyzing(false)
      alert((e as Error).message)
    }
  }

  const handleOpenPrompt = async () => {
    if (!promptLoaded) {
      try { setCustomPrompt(await promptApi(activeSlug).getAnalysisPrompt()) } catch { /* 무시 */ }
      setPromptLoaded(true)
    }
    setPromptOpen((v) => !v)
  }

  const handleNotify = async (force = false) => {
    if (!video) return
    if (video.analysis_status !== 'done') { alert('분석 완료 후 발송할 수 있습니다.'); return }
    if (video.notified_at && !force && !window.confirm('이미 발송된 영상입니다. 다시 발송할까요?')) return
    setNotifying(true)
    try {
      const res = await videoApi(activeSlug).notify(Number(videoPk), force || Boolean(video.notified_at))
      await silentRefresh()
      alert(res.message)
    } catch (e) { alert((e as Error).message) }
    finally { setNotifying(false) }
  }

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!video) return null

  return (
    <div className="space-y-5 max-w-3xl mx-auto w-full">
      {/* 상단 네비 */}
      <div className="flex items-center gap-2 text-sm text-gray-500 min-w-0">
        <Link to={`/g/${activeSlug}/videos`} className="hover:text-blue-600 shrink-0">영상 목록</Link>
        <span className="shrink-0">/</span>
        <span className="text-gray-700 truncate min-w-0">{video.title}</span>
      </div>

      <div className="space-y-5">
        {/* 영상 헤더 */}
        <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            {video.thumbnail_url && (
              <img src={video.thumbnail_url} alt={video.title} className="w-full aspect-video object-cover" />
            )}
            <div className="p-4 sm:p-5 space-y-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <h1 className="text-lg sm:text-xl font-bold text-gray-900 leading-snug min-w-0 flex-1 order-2 sm:order-1">
                  {video.title}
                </h1>
                <div className="flex flex-wrap gap-2 shrink-0 order-1 sm:order-2 sm:justify-end">
                  <a
                    href={video.video_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-3 py-1.5 bg-red-600 text-white text-xs rounded-lg hover:bg-red-700 font-medium"
                  >
                    YouTube에서 보기
                  </a>
                  <button onClick={handleOpenPrompt} disabled={reanalyzing || notifying}
                    className={`px-3 py-1.5 text-xs rounded-lg font-medium disabled:opacity-60 ${promptOpen ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
                    {promptOpen ? '프롬프트 닫기' : '프롬프트 수정'}
                  </button>
                  <button
                    onClick={handleReanalyze}
                    disabled={reanalyzing || deleting}
                    className="px-3 py-1.5 bg-blue-50 text-blue-600 text-xs rounded-lg hover:bg-blue-100 disabled:opacity-60 font-medium"
                  >
                    {reanalyzing ? '분석 중...' : promptOpen ? '이 프롬프트로 재분석' : '재분석'}
                  </button>
                  <button onClick={() => handleNotify(false)} disabled={notifying || reanalyzing || video.analysis_status !== 'done'}
                    className="px-3 py-1.5 bg-sky-50 text-sky-700 text-xs rounded-lg hover:bg-sky-100 disabled:opacity-60 font-medium">
                    {notifying ? '발송 중...' : video.notified_at ? 'Telegram 재발송' : 'Telegram 발송'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteConfirm(true)}
                    disabled={reanalyzing || deleting}
                    className="px-3 py-1.5 bg-red-50 text-red-500 text-xs rounded-lg hover:bg-red-100 disabled:opacity-60 font-medium"
                  >
                    삭제
                  </button>
                </div>
              </div>

              <div className="flex items-center gap-3 flex-wrap text-xs text-gray-500">
                <StatusBadge status={video.analysis_status} />
                {video.source_channel_name && (
                  <span className="text-purple-600 bg-purple-50 border border-purple-200 px-2 py-0.5 rounded-full">
                    추가 · {video.source_channel_name}
                  </span>
                )}
                <span>📅 {dayjs(video.published_at).format('YYYY-MM-DD HH:mm')}</span>
                {video.view_count != null && <span>👁 {video.view_count.toLocaleString()}</span>}
                {video.like_count != null && <span>👍 {video.like_count.toLocaleString()}</span>}
                {video.duration_seconds != null && (
                  <span>⏱ {Math.floor(video.duration_seconds / 60)}분 {video.duration_seconds % 60}초</span>
                )}
              </div>

              {/* 태그 */}
              {video.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {video.tags.map((t) => (
                    <Link
                      key={t}
                      to={`/g/${activeSlug}/videos?tag=${encodeURIComponent(t)}`}
                      className="px-2.5 py-0.5 bg-gray-100 hover:bg-blue-50 hover:text-blue-600 text-gray-600 rounded-full text-xs transition-colors"
                    >
                      {t}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 커스텀 프롬프트 패널 */}
          {promptOpen && (
            <div className="mt-3 border border-amber-200 rounded-lg bg-amber-50 p-3 space-y-2">
              <p className="text-xs font-semibold text-amber-700">이 영상 전용 분석 프롬프트 (기본 프롬프트 기반)</p>
              <textarea value={customPrompt} onChange={(e) => setCustomPrompt(e.target.value)} rows={10} spellCheck={false}
                className="w-full border border-amber-300 rounded-lg px-3 py-2 text-xs font-mono bg-white resize-y" />
            </div>
          )}

          {/* YouTube 원본 설명문 */}
          {video.description && (
            <details className="bg-white rounded-xl shadow-sm p-5 group">
              <summary className="font-semibold text-gray-800 cursor-pointer select-none list-none flex items-center justify-between">
                <span>YouTube 설명문</span>
                <span className="text-gray-400 text-sm group-open:rotate-180 transition-transform">▼</span>
              </summary>
              <pre className="mt-3 text-xs text-gray-600 whitespace-pre-wrap break-words font-sans leading-relaxed max-h-60 overflow-y-auto">
                {video.description}
              </pre>
            </details>
          )}

          {/* 분석 결과 */}
          {video.analysis_sections && video.analysis_sections.length > 0 ? (
            <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5">
              <h2 className="font-semibold text-gray-800 mb-4">상세 분석</h2>
              <div className="space-y-5">
                {video.analysis_sections.map((sec) => (
                  <section key={sec.key}>
                    {sec.title && (
                      <h3 className="font-semibold text-gray-800 mb-2">{sec.title}</h3>
                    )}
                    <ul className="space-y-1.5">
                      {sec.bullets.map((b, i) => (
                        <li key={i} className="flex gap-2 text-sm text-gray-700">
                          <span className="text-blue-400 shrink-0">•</span>
                          <article className="prose prose-sm max-w-none min-w-0 break-words">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{b}</ReactMarkdown>
                          </article>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            </div>
          ) : video.full_analysis_md ? (
            <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5">
              <h2 className="font-semibold text-gray-800 mb-4">상세 분석</h2>
              <article className="prose prose-sm max-w-none text-gray-700 break-words overflow-x-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{video.full_analysis_md}</ReactMarkdown>
              </article>
            </div>
          ) : video.analysis_status === 'pending' || video.analysis_status === 'processing' ? (
            <div className="bg-white rounded-xl shadow-sm p-8 text-center text-gray-400">
              <div className="text-4xl mb-2">⏳</div>
              <p>분석이 진행 중입니다...</p>
            </div>
          ) : null}

        {/* 상세 분석 아래: 요약 → 분석 정보 → 오류 */}
        {(video.headline ||
          video.one_line ||
          video.short_summary_md ||
          (video.bullet_points && video.bullet_points.length > 0)) && (
          <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5 space-y-3">
            <h2 className="font-semibold text-gray-800">요약</h2>
            {video.headline && (
              <p className="font-medium text-gray-900">{video.headline}</p>
            )}
            {video.one_line && (
              <p className="text-sm text-gray-600 italic">{video.one_line}</p>
            )}
            {video.short_summary_md && (
              <article className="prose prose-sm max-w-none text-gray-700 break-words">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{video.short_summary_md}</ReactMarkdown>
              </article>
            )}
            {video.bullet_points && video.bullet_points.length > 0 && (
              <ul className="space-y-1">
                {video.bullet_points.map((bp, i) => (
                  <li key={i} className="flex gap-2 text-sm text-gray-700">
                    <span className="text-blue-400 shrink-0">•</span>
                    <span className="min-w-0 break-words">{bp}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {(video.sentiment || video.confidence_score != null || video.model_name) && (
          <div className="bg-white rounded-xl shadow-sm p-4 sm:p-5 space-y-3">
            <h2 className="font-semibold text-gray-800">분석 정보</h2>
            {video.sentiment && (
              <div className="flex justify-between gap-4 text-sm min-w-0">
                <span className="text-gray-500 shrink-0">감성</span>
                <span className="font-medium text-gray-700 text-right break-words">{video.sentiment}</span>
              </div>
            )}
            {video.confidence_score != null && (
              <div className="space-y-1">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-500">신뢰도</span>
                </div>
                <ConfidenceBar score={video.confidence_score} />
              </div>
            )}
            {video.model_name && (
              <div className="flex justify-between gap-4 text-sm min-w-0">
                <span className="text-gray-500 shrink-0">모델</span>
                <span className="font-medium text-gray-700 text-xs text-right break-all">{video.model_name}</span>
              </div>
            )}
            {video.analyzed_at && (
              <div className="flex justify-between gap-4 text-sm min-w-0">
                <span className="text-gray-500 shrink-0">분석 시각</span>
                <span className="text-gray-700 text-xs shrink-0">{dayjs(video.analyzed_at).format('MM/DD HH:mm')}</span>
              </div>
            )}
          </div>
        )}

        {video.analysis_error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-xs text-red-700 space-y-1 break-words">
            <p className="font-semibold">분석 오류</p>
            <p className="font-mono whitespace-pre-wrap">{video.analysis_error}</p>
            {video.retry_count != null && <p className="text-gray-500">재시도: {video.retry_count}회</p>}
          </div>
        )}

        {/* Telegram 알림 미리보기 — 실제 발송 본문과 동일(그룹 메시지 템플릿 기준).
            백엔드 build_message가 렌더한 텔레그램 HTML(b/i/code/a 한정, 동적 내용은
            모두 이스케이프됨)을 그대로 표시한다. */}
        {previewHtml && (
          <div className="bg-gray-800 rounded-xl p-4 text-gray-100 text-xs space-y-2 break-words">
            <p className="text-gray-400 uppercase tracking-wide">Telegram 알림 미리보기</p>
            <div
              className="telegram-preview text-gray-100 whitespace-pre-wrap font-sans leading-relaxed max-h-80 overflow-y-auto border border-gray-600 rounded-lg p-3"
              dangerouslySetInnerHTML={{ __html: previewHtml }}
            />
            {video.notified_at && <p className="text-green-400">✅ 발송됨: {dayjs(video.notified_at).format('MM/DD HH:mm')}</p>}
          </div>
        )}
      </div>

      {deleteConfirm && (
        <ConfirmModal
          title="영상 삭제 확인"
          message="이 영상과 연관된 분석·태그 데이터가 삭제됩니다. 계속하시겠습니까?"
          detail={video.title}
          confirmLabel="삭제"
          busy={deleting}
          busyLabel="삭제 중..."
          onConfirm={handleDelete}
          onClose={() => setDeleteConfirm(false)}
        />
      )}
    </div>
  )
}
