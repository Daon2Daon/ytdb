import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { videoApi } from '../api/videos'
import { groupClient } from '../api/http'
import type { InstantAnalyzeResponse } from '../api/types'
import { useGroup } from '../group/useGroup'

const PLACEHOLDER = 'https://www.youtube.com/watch?v=...'

type Phase = 'idle' | 'loading' | 'analyzing' | 'done' | 'error'

export default function InstantAnalyze() {
  const { activeSlug } = useGroup()
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState('')
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }

  // 언마운트 시 폴링 정리(이동 후 stale navigate 방지).
  useEffect(() => () => stopPolling(), [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return

    setPhase('loading')
    setMessage('')
    stopPolling()

    try {
      const res = await groupClient(activeSlug).post<InstantAnalyzeResponse>('/videos/instant', { video_url: url.trim() })
      // 신규/기존 모두 백엔드가 분석을 재대기열에 등록하므로 완료까지 폴링 후 이동한다.
      setMessage(
        res.existing
          ? '기존 영상을 다시 분석합니다. 완료되면 결과로 이동합니다.'
          : '분석 대기열에 등록되었습니다. 완료되면 결과로 이동합니다.',
      )
      setPhase('analyzing')
      const videoPk = res.video_pk
      pollingRef.current = setInterval(async () => {
        try {
          const detail = await videoApi(activeSlug).get(videoPk)
          if (detail.analysis_status === 'done' || detail.analysis_status === 'failed') {
            stopPolling()
            setPhase('done')
            setTimeout(() => navigate(`/g/${activeSlug}/videos/${videoPk}`), 800)
          }
        } catch { /* 폴링 실패 무시 */ }
      }, 3000)
      setTimeout(() => {
        if (pollingRef.current) {
          stopPolling()
          setPhase('done')
          navigate(`/g/${activeSlug}/videos/${videoPk}`)
        }
      }, 5 * 60 * 1000)
    } catch (err) {
      setPhase('error')
      setMessage((err as Error).message)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">영상 분석</h1>
        <p className="mt-1 text-sm text-gray-500">
          채널 등록 없이 YouTube URL을 직접 입력해 분석합니다.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-sm p-6 space-y-4">
        {/* URL 입력 */}
        <div className="space-y-1.5">
          <label className="block text-sm font-medium text-gray-700">YouTube URL</label>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={PLACEHOLDER}
            disabled={phase === 'loading' || phase === 'analyzing'}
            className="w-full border border-gray-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <p className="text-xs text-gray-400">
            watch?v=, youtu.be/, /shorts/ 형식 지원
          </p>
        </div>

        {/* 상태 메시지 */}
        {message && (
          <div
            className={`text-sm rounded-lg px-4 py-3 ${
              phase === 'error'
                ? 'bg-red-50 text-red-700 border border-red-200'
                : 'bg-blue-50 text-blue-700 border border-blue-100'
            }`}
          >
            {message}
          </div>
        )}

        {/* 분석 중 진행 표시 */}
        {phase === 'analyzing' && (
          <div className="flex items-center gap-3 text-sm text-gray-600 bg-gray-50 rounded-lg px-4 py-3">
            <svg className="animate-spin w-5 h-5 text-blue-500 shrink-0" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <span>LLM 분석 중입니다. 완료되면 결과 페이지로 이동합니다...</span>
          </div>
        )}

        {/* 제출 버튼 */}
        <button
          type="submit"
          disabled={!url.trim() || phase === 'loading' || phase === 'analyzing'}
          className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-lg text-sm transition-colors"
        >
          {phase === 'loading'
            ? '영상 정보 조회 중...'
            : phase === 'analyzing'
            ? '분석 중...'
            : '분석 시작'}
        </button>
      </form>

      {/* 안내 */}
      <div className="bg-gray-50 rounded-xl p-4 text-sm text-gray-500 space-y-1.5">
        <p className="font-medium text-gray-700">안내</p>
        <ul className="list-disc list-inside space-y-1">
          <li>분석은 일반 채널 영상과 동일한 파이프라인으로 진행됩니다.</li>
          <li>이미 분석된 URL을 입력하면 기존 결과 페이지로 이동합니다.</li>
          <li>추가 영상은 알림(텔레그램)이 발송되지 않습니다.</li>
          <li>분석 결과는 영상 목록에서 확인하거나 재분석할 수 있습니다.</li>
        </ul>
      </div>
    </div>
  )
}
