import { useCallback, useEffect, useState } from 'react'
import { entitiesApi, type MergeCandidate } from '../api/entities'

interface Props {
  slug: string
}

export default function MergeQueue({ slug }: Props) {
  const [rows, setRows] = useState<MergeCandidate[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await entitiesApi(slug).mergeCandidates())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [slug])

  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return null  // 병합 큐는 부가 기능 — 조회 실패 시 조용히 숨김
  if (!rows.length) return null

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
      <h2 className="font-semibold text-gray-800">엔티티 병합 승인 대기</h2>
      <ul className="space-y-2">
        {rows.map((r) =>
          r.candidates.map((alias) => (
            <li key={`${r.entity_pk}:${alias}`}
              className="flex items-center gap-2 text-sm text-gray-700">
              <span className="flex-1">{alias} → <b>{r.canonical_name}</b></span>
              <button type="button" disabled={busy}
                onClick={() => act(() => entitiesApi(slug).approve(r.entity_pk, alias))}
                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                승인
              </button>
              <button type="button" disabled={busy}
                onClick={() => act(() => entitiesApi(slug).reject(r.entity_pk, alias))}
                className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50">
                거절
              </button>
            </li>
          )))}
      </ul>
    </div>
  )
}
