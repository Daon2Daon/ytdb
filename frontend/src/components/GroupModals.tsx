import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { groupApi } from '../api/groups'
import { useGroup } from '../group/useGroup'

function ModalShell({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full space-y-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-bold text-gray-900">{title}</h3>
        {children}
      </div>
    </div>
  )
}

export function NewGroupModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { reloadGroups } = useGroup()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [schema, setSchema] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      await groupApi.create({ slug: slug.trim(), name: name.trim(), schema_name: schema.trim() || undefined })
      await reloadGroups()
      onClose()
      navigate(`/g/${slug.trim()}/`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="새 그룹" onClose={onClose}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (소문자/숫자/밑줄)</label>
          <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="invest"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="투자 모니터"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">DB 스키마 이름 (선택, 기본 youtube_&#123;ID&#125;)</label>
          <input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="youtube_invest"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !slug.trim() || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '생성 중...' : '생성'}
        </button>
      </div>
    </ModalShell>
  )
}

export function EditGroupModal({ onClose }: { onClose: () => void }) {
  const { activeGroup, activeSlug, reloadGroups } = useGroup()
  const [name, setName] = useState(activeGroup?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await groupApi.rename(activeSlug, name.trim())
      await reloadGroups()
      onClose()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="그룹 이름 수정" onClose={onClose}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (변경 불가)</label>
        <input value={activeSlug} disabled className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-400" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '저장 중...' : '저장'}
        </button>
      </div>
    </ModalShell>
  )
}
