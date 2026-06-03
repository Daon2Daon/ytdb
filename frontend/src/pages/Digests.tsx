import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import type { Digest } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function Digests() {
  const { activeSlug } = useGroup()
  const [items, setItems] = useState<Digest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await digestApi(activeSlug).list())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      await digestApi(activeSlug).generate()
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const handleDelete = async (pk: number) => {
    if (!window.confirm('이 주간 리뷰를 삭제할까요?')) return
    try {
      await digestApi(activeSlug).remove(pk)
      setItems((prev) => prev.filter((d) => d.digest_pk !== pk))
    } catch (e) {
      alert((e as Error).message)
    }
  }

  if (loading) return <Spinner />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">주간 리뷰</h1>
        <button onClick={handleGenerate} disabled={generating}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60">
          {generating ? '생성 중...' : '지금 생성'}
        </button>
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {items.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm py-16 text-center text-gray-400">
          <p className="text-5xl mb-3">📊</p>
          <p>주간 리뷰가 없습니다. "지금 생성"으로 만들어 보세요.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((d) => (
            <div key={d.digest_pk} className="bg-white rounded-xl shadow-sm p-4 flex items-center gap-4">
              <Link to={`/g/${activeSlug}/digests/${d.digest_pk}`} className="flex-1 min-w-0">
                <p className="font-medium text-gray-900 truncate">{d.headline || '주간 리뷰'}</p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {dayjs(d.period_start).format('YYYY-MM-DD')} ~ {dayjs(d.period_end).format('YYYY-MM-DD')}
                  {' · '}영상 {d.video_count}건 · {d.status}
                </p>
              </Link>
              <button onClick={() => handleDelete(d.digest_pk)}
                className="px-2.5 py-1.5 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100 shrink-0">삭제</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
