import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { tagApi } from '../api/tags'
import type { Tag } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

function scaleFont(count: number, min: number, max: number): number {
  if (max === min) return 16
  return Math.round(12 + ((count - min) / (max - min)) * 24)
}

export default function Tags() {
  const { activeSlug } = useGroup()
  const navigate = useNavigate()
  const [tags, setTags] = useState<Tag[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setTags(await tagApi(activeSlug).list(1, 200))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />

  const counts = tags.map((t) => t.video_count)
  const minCount = Math.min(...counts, 0)
  const maxCount = Math.max(...counts, 1)
  const openTag = (name: string) => navigate(`/g/${activeSlug}/videos?tag=${encodeURIComponent(name)}`)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">태그 클라우드</h1>
        <span className="text-sm text-gray-400">태그 {tags.length}개</span>
      </div>

      {tags.length === 0 ? (
        <div className="bg-white rounded-xl py-16 text-center text-gray-400 shadow-sm">
          <p className="text-5xl mb-3">🏷</p>
          <p>아직 태그가 없습니다.</p>
        </div>
      ) : (
        <>
          <div className="bg-white rounded-xl shadow-sm p-6">
            <div className="flex flex-wrap gap-3 items-end">
              {tags.map((t) => {
                const fontSize = scaleFont(t.video_count, minCount, maxCount)
                const opacity = 0.4 + 0.6 * ((t.video_count - minCount) / Math.max(maxCount - minCount, 1))
                return (
                  <button
                    key={t.tag_pk}
                    onClick={() => openTag(t.name)}
                    style={{ fontSize: `${fontSize}px`, opacity }}
                    className="text-blue-600 hover:text-blue-800 hover:opacity-100 transition-all font-medium leading-none"
                    title={`${t.video_count}개 영상`}
                  >
                    {t.name}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">태그</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">유형</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">영상 수</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {tags.map((t) => (
                  <tr key={t.tag_pk} className="hover:bg-gray-50">
                    <td className="px-4 py-2.5 font-medium text-gray-800">{t.name}</td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs">{t.tag_type}</td>
                    <td className="px-4 py-2.5 text-right text-gray-700">{t.video_count}</td>
                    <td className="px-4 py-2.5 text-right">
                      <button onClick={() => openTag(t.name)} className="text-blue-600 hover:underline text-xs">
                        영상 보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
