import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { channelApi } from '../api/channels'
import type { Channel } from '../api/types'
import { useGroup } from '../group/useGroup'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import ConfirmModal from '../components/ConfirmModal'

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${checked ? 'bg-blue-600' : 'bg-gray-300'}`}
    >
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-5' : 'translate-x-1'}`} />
    </button>
  )
}

export default function Channels() {
  const { activeSlug } = useGroup()
  const [channels, setChannels] = useState<Channel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [addForm, setAddForm] = useState({ channel_input: '', category: '', poll_interval_min: 720, backfill: false })
  const [addLoading, setAddLoading] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null)
  const [pollingPk, setPollingPk] = useState<number | null>(null)
  const [savingPollPk, setSavingPollPk] = useState<number | null>(null)
  const [savingCategoryPk, setSavingCategoryPk] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setChannels(await channelApi(activeSlug).list())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    setAddLoading(true)
    setAddError(null)
    try {
      const ch = await channelApi(activeSlug).add({
        channel_input: addForm.channel_input,
        category: addForm.category || undefined,
        poll_interval_min: addForm.poll_interval_min,
        backfill: addForm.backfill,
      })
      setChannels((prev) => [ch, ...prev])
      setAdding(false)
      setAddForm({ channel_input: '', category: '', poll_interval_min: 720, backfill: false })
    } catch (e) {
      setAddError((e as Error).message)
    } finally {
      setAddLoading(false)
    }
  }

  const handleToggleActive = async (ch: Channel) => {
    try {
      const updated = await channelApi(activeSlug).update(ch.channel_pk, { is_active: !ch.is_active })
      setChannels((prev) => prev.map((c) => c.channel_pk === ch.channel_pk ? updated : c))
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const handleToggleNotify = async (ch: Channel) => {
    try {
      const updated = await channelApi(activeSlug).update(ch.channel_pk, { notify_enabled: !ch.notify_enabled })
      setChannels((prev) => prev.map((c) => c.channel_pk === ch.channel_pk ? updated : c))
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await channelApi(activeSlug).remove(deleteTarget.channel_pk)
      setChannels((prev) => prev.filter((c) => c.channel_pk !== deleteTarget.channel_pk))
      setDeleteTarget(null)
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const handlePoll = async (ch: Channel) => {
    setPollingPk(ch.channel_pk)
    try {
      const r = await channelApi(activeSlug).poll(ch.channel_pk)
      alert(r.message)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setPollingPk(null)
    }
  }

  const handleCategoryBlur = async (
    ch: Channel,
    raw: string,
    inputEl: HTMLInputElement
  ) => {
    const value = raw.trim()
    const current = ch.category ?? ''
    if (value === current) return
    setSavingCategoryPk(ch.channel_pk)
    try {
      const updated = await channelApi(activeSlug).update(ch.channel_pk, { category: value || null })
      setChannels((prev) => prev.map((c) => (c.channel_pk === ch.channel_pk ? updated : c)))
    } catch (e) {
      alert((e as Error).message)
      inputEl.value = current
    } finally {
      setSavingCategoryPk(null)
    }
  }

  const handlePollIntervalBlur = async (
    ch: Channel,
    raw: string,
    inputEl: HTMLInputElement
  ) => {
    const v = Number(raw)
    if (!Number.isFinite(v) || v < 10) {
      alert('모니터링 주기는 10분 이상이어야 합니다.')
      inputEl.value = String(ch.poll_interval_min)
      return
    }
    if (v > 10080) {
      alert('모니터링 주기는 최대 10080분(7일)입니다.')
      inputEl.value = String(ch.poll_interval_min)
      return
    }
    if (v === ch.poll_interval_min) return
    setSavingPollPk(ch.channel_pk)
    try {
      const updated = await channelApi(activeSlug).update(ch.channel_pk, { poll_interval_min: v })
      setChannels((prev) => prev.map((c) => (c.channel_pk === ch.channel_pk ? updated : c)))
    } catch (e) {
      alert((e as Error).message)
      inputEl.value = String(ch.poll_interval_min)
    } finally {
      setSavingPollPk(null)
    }
  }

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">채널 관리</h1>
        <button
          onClick={() => setAdding(!adding)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
        >
          + 채널 추가
        </button>
      </div>

      {/* 채널 추가 폼 */}
      {adding && (
        <form onSubmit={handleAdd} className="bg-white rounded-xl shadow-sm p-5 space-y-4">
          <h2 className="font-semibold text-gray-800">새 채널 추가</h2>
          {addError && <ErrorBanner message={addError} />}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">채널 입력 *</label>
              <input
                type="text"
                placeholder="@handle / 채널 ID / URL"
                value={addForm.channel_input}
                onChange={(e) => setAddForm({ ...addForm, channel_input: e.target.value })}
                required
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">카테고리</label>
              <input
                type="text"
                placeholder="뉴스, 기술, ..."
                value={addForm.category}
                onChange={(e) => setAddForm({ ...addForm, category: e.target.value })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">모니터링 주기 (분)</label>
              <input
                type="number"
                min={10}
                value={addForm.poll_interval_min}
                onChange={(e) => setAddForm({ ...addForm, poll_interval_min: Number(e.target.value) })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input type="checkbox" checked={addForm.backfill} onChange={(e) => setAddForm({ ...addForm, backfill: e.target.checked })} />
              과거 영상 수집(등록 시 1회)
            </label>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => setAdding(false)}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={addLoading}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
            >
              {addLoading ? '추가 중...' : '추가'}
            </button>
          </div>
        </form>
      )}

      {/* 채널 목록 */}
      {channels.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm py-16 text-center text-gray-400">
          <p className="text-5xl mb-3">📺</p>
          <p>등록된 채널이 없습니다. 채널을 추가해 보세요.</p>
        </div>
      ) : (() => {
        const activeChannels = channels.filter((ch) => ch.is_active)
        const inactiveChannels = channels.filter((ch) => !ch.is_active)
        const hasGroups = activeChannels.length > 0 && inactiveChannels.length > 0

        const renderRow = (ch: Channel) => (
          <tr key={ch.channel_pk} className={`hover:bg-gray-50 ${!ch.is_active ? 'bg-gray-50/60' : ''}`}>
            <td className="px-4 py-3">
              <div className="flex items-center gap-2">
                {ch.thumbnail_url && (
                  <img src={ch.thumbnail_url} alt="" className="w-8 h-8 rounded-full object-cover" />
                )}
                <div>
                  <p className={`font-medium ${ch.is_active ? 'text-gray-900' : 'text-gray-500'}`}>{ch.channel_name}</p>
                  {ch.channel_handle && <p className="text-xs text-gray-400">{ch.channel_handle}</p>}
                </div>
              </div>
            </td>
            <td className="text-center px-3 py-3">
              <ToggleSwitch checked={ch.is_active} onChange={() => handleToggleActive(ch)} />
            </td>
            <td className="text-center px-3 py-3">
              <ToggleSwitch checked={ch.notify_enabled} onChange={() => handleToggleNotify(ch)} />
            </td>
            <td className="px-3 py-3">
              <input
                type="text"
                placeholder="-"
                disabled={savingCategoryPk === ch.channel_pk}
                key={`${ch.channel_pk}-category-${ch.category}`}
                defaultValue={ch.category ?? ''}
                onBlur={(e) => handleCategoryBlur(ch, e.target.value, e.target)}
                className="w-28 border border-gray-200 rounded px-2 py-1 text-sm text-gray-800 placeholder-gray-300 disabled:opacity-50"
                title="값을 바꾼 뒤 다른 곳을 클릭하면 저장됩니다"
              />
            </td>
            <td className="text-right px-3 py-3">
              <input
                type="number"
                min={10}
                max={10080}
                disabled={savingPollPk === ch.channel_pk || !ch.is_active}
                key={`${ch.channel_pk}-poll-${ch.poll_interval_min}`}
                defaultValue={ch.poll_interval_min}
                onBlur={(e) => handlePollIntervalBlur(ch, e.target.value, e.target)}
                className="w-24 text-right border border-gray-200 rounded px-2 py-1 text-sm text-gray-800 disabled:opacity-50"
                title={ch.is_active ? '값을 바꾼 뒤 다른 곳을 클릭하면 저장됩니다' : '비활성 채널은 모니터링 주기를 사용하지 않습니다'}
              />
            </td>
            <td className="px-3 py-3 text-gray-400 text-xs">
              {ch.last_checked_at ? dayjs(ch.last_checked_at).format('MM/DD HH:mm') : '-'}
            </td>
            <td className="px-3 py-3">
              <div className="flex items-center gap-1 justify-end">
                <button
                  onClick={() => handlePoll(ch)}
                  disabled={pollingPk === ch.channel_pk || !ch.is_active}
                  title={!ch.is_active ? '비활성 채널은 자동 모니터링을 지원하지 않습니다' : undefined}
                  className="px-2 py-1 text-xs rounded bg-blue-50 text-blue-600 hover:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {pollingPk === ch.channel_pk ? '...' : '모니터링'}
                </button>
                <button
                  onClick={() => setDeleteTarget(ch)}
                  className="px-2 py-1 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100"
                >
                  삭제
                </button>
              </div>
            </td>
          </tr>
        )

        const renderSectionHeader = (label: string) => (
          <tr key={label}>
            <td colSpan={7} className="px-4 py-2 text-[11px] font-semibold text-gray-400 tracking-wide uppercase bg-gray-50 border-t border-b border-gray-200">
              {label}
            </td>
          </tr>
        )

        return (
          <div className="bg-white rounded-xl shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">채널</th>
                  <th className="text-center px-3 py-3 font-medium text-gray-600">활성</th>
                  <th className="text-center px-3 py-3 font-medium text-gray-600">알림</th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">
                    카테고리
                    <span className="block text-[10px] font-normal text-gray-400 normal-case">포커스 해제 시 저장</span>
                  </th>
                  <th className="text-right px-3 py-3 font-medium text-gray-600">
                    모니터링(분)
                    <span className="block text-[10px] font-normal text-gray-400 normal-case">포커스 해제 시 저장</span>
                  </th>
                  <th className="text-left px-3 py-3 font-medium text-gray-600">최근 모니터링</th>
                  <th className="px-3 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {hasGroups && renderSectionHeader(`모니터링 채널 (${activeChannels.length})`)}
                {activeChannels.map(renderRow)}
                {inactiveChannels.length > 0 && renderSectionHeader(`비활성 채널 (${inactiveChannels.length})`)}
                {inactiveChannels.map(renderRow)}
              </tbody>
            </table>
          </div>
        )
      })()}

      {/* 삭제 확인 모달 */}
      {deleteTarget && (
        <ConfirmModal
          title="채널 삭제 확인"
          message={
            <>
              <strong>{deleteTarget.channel_name}</strong> 채널과 연관된 모든 영상, 분석 데이터가
              삭제됩니다. 계속하시겠습니까?
            </>
          }
          confirmLabel="삭제"
          onConfirm={handleDelete}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}
