import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { groupApi } from '../api/groups'
import { useAuth } from '../auth/useAuth'
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
  const { user } = useAuth()
  const { reloadGroups } = useGroup()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [schema, setSchema] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // 일반 사용자는 백엔드가 slug/스키마를 자동 생성하고 입력값을 무시하므로
  // 해당 입력란 자체를 보여주지 않는다(입력해도 반영되지 않는 혼란 방지).
  const isAdmin = user?.role === 'admin'

  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      const created = await groupApi.create(
        isAdmin
          ? { slug: slug.trim(), name: name.trim(), schema_name: schema.trim() || undefined }
          : { name: name.trim() },
      )
      await reloadGroups()
      onClose()
      navigate(`/g/${created.slug}/`)
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
        {isAdmin && (
          <div>
            <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (소문자/숫자/밑줄)</label>
            <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="invest"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
          </div>
        )}
        <div>
          <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        {isAdmin && (
          <div>
            <label className="block text-xs text-gray-500 mb-1">DB 스키마 이름 (선택, 기본 youtube_&#123;ID&#125;)</label>
            <input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="youtube_invest"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
          </div>
        )}
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !name.trim() || (isAdmin && !slug.trim())}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '생성 중...' : '생성'}
        </button>
      </div>
    </ModalShell>
  )
}

export function EditGroupModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { activeGroup, activeSlug, reloadGroups } = useGroup()
  const [name, setName] = useState(activeGroup?.name ?? '')
  const [isActive, setIsActive] = useState(activeGroup?.is_active ?? true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // 삭제 확인 단계: null=미진입, string=사용자가 입력 중인 확인 텍스트
  const [confirmText, setConfirmText] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await groupApi.update(activeSlug, { name: name.trim(), is_active: isActive })
      await reloadGroups()
      onClose()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    setErr(null)
    try {
      await groupApi.remove(activeSlug)
      onClose()
      // reloadGroups 후 GroupProvider가 남은 첫 그룹 또는 루트로 보정한다.
      await reloadGroups()
      navigate('/', { replace: true })
    } catch (e) {
      setErr((e as Error).message)
      setBusy(false)
    }
  }

  const groupName = activeGroup?.name ?? ''

  return (
    <ModalShell title="그룹 수정" onClose={onClose}>
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
      <div className="border border-gray-200 rounded-lg p-3 space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <button
            type="button"
            onClick={() => setIsActive((v) => !v)}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${isActive ? 'bg-blue-600' : 'bg-gray-300'}`}
          >
            <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${isActive ? 'translate-x-6' : 'translate-x-1'}`} />
          </button>
          <span className="text-sm font-medium text-gray-700">{isActive ? '활성 (자동화 동작)' : '비활성 (일시정지)'}</span>
        </label>
        {!isActive && (
          <p className="text-xs text-amber-600">
            자동 수집·분석·다이제스트·알림이 중단됩니다. 데이터 조회와 수동 실행은 계속 가능합니다.
          </p>
        )}
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '저장 중...' : '저장'}
        </button>
      </div>
      <div className="border-t border-gray-200 pt-4">
        {confirmText === null ? (
          <button
            onClick={() => setConfirmText('')}
            className="px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50"
          >
            그룹 삭제
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-red-600 font-medium">
              수집된 영상·분석 데이터가 모두 영구 삭제됩니다. 되돌릴 수 없습니다.
            </p>
            <label className="block text-xs text-gray-500">
              계속하려면 그룹 명칭 <span className="font-bold text-gray-700">{groupName}</span> 을(를) 입력하세요.
            </label>
            <input
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={groupName}
              className="w-full border border-red-300 rounded-lg px-3 py-2 text-sm"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setConfirmText(null)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
              <button
                onClick={remove}
                disabled={busy || confirmText !== groupName}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700 disabled:opacity-60"
              >
                {busy ? '삭제 중...' : '영구 삭제'}
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  )
}
