import { useCallback, useEffect, useState } from 'react'
import { adminApi, type AdminUser, type Invite, type PlanInfo, type UserLimits } from '../../api/admin'

const emptyLimitsForm = {
  max_groups: '',
  max_channels_total: '',
  max_analyses_per_day: '',
  max_video_minutes: '',
  min_poll_interval_min: '',
  note: '',
}

type LimitsForm = typeof emptyLimitsForm

function toNullableNumber(v: string): number | null {
  return v.trim() === '' ? null : Number(v)
}

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000

function expiryHint(iso: string): { label: string; className: string } {
  const diff = new Date(iso).getTime() - Date.now()
  if (diff < 0) return { label: '만료', className: 'text-red-600' }
  if (diff <= SEVEN_DAYS_MS) return { label: '임박', className: 'text-amber-600' }
  return { label: '유효', className: 'text-gray-400' }
}

function Badge({ tone, children }: { tone: 'green' | 'red' | 'amber' | 'gray'; children: string }) {
  const tones = {
    green: 'bg-green-50 text-green-700 border-green-200',
    red: 'bg-red-50 text-red-700 border-red-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    gray: 'bg-gray-50 text-gray-600 border-gray-200',
  }
  return (
    <span className={`text-xs border rounded-full px-2 py-0.5 whitespace-nowrap ${tones[tone]}`}>
      {children}
    </span>
  )
}

const actionBtn = 'border border-gray-300 rounded-lg px-2.5 py-1 text-xs hover:bg-gray-50 whitespace-nowrap'

/** 사용자 관리 + 초대(사용자를 만드는 입구라 같은 탭). */
export default function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [plans, setPlans] = useState<PlanInfo[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [error, setError] = useState<string | null>(null)
  const [memo, setMemo] = useState('')
  const [planSlug, setPlanSlug] = useState<string>('')
  const [createdUrl, setCreatedUrl] = useState<string | null>(null)
  const [tempPw, setTempPw] = useState<Record<number, string>>({})
  const [editingLimits, setEditingLimits] = useState<number | null>(null)
  const [limitsForm, setLimitsForm] = useState<LimitsForm>(emptyLimitsForm)

  const load = useCallback(async () => {
    try {
      const [u, p, i] = await Promise.all([adminApi.users(), adminApi.plans(), adminApi.invites()])
      setUsers(u); setPlans(p); setInvites(i); setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const createInvite = async () => {
    try {
      const r = await adminApi.createInvite(planSlug || null, memo, 7)
      setCreatedUrl(r.signup_url)
      setMemo('')
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const revoke = async (id: number) => {
    try { await adminApi.revokeInvite(id); await load() } catch (e) { setError((e as Error).message) }
  }

  const planName = (id: number) => plans.find((p) => p.plan_id === id)?.name ?? id

  const toggleStatus = async (u: AdminUser) => {
    try {
      await adminApi.patchUser(u.user_id, { status: u.status === 'active' ? 'suspended' : 'active' })
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const changePlan = async (u: AdminUser, planId: number) => {
    try {
      await adminApi.patchUser(u.user_id, { plan_id: planId })
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const changeExpiry = async (u: AdminUser, value: string) => {
    try {
      await adminApi.patchUser(u.user_id, {
        plan_expires_at: value === '' ? null : new Date(value + 'Z').toISOString(),
      })
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const issueTempPassword = async (u: AdminUser) => {
    try {
      const r = await adminApi.issueTempPassword(u.user_id)
      setTempPw((prev) => ({ ...prev, [u.user_id]: r.temp_password }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const startEditLimits = (u: AdminUser) => {
    setEditingLimits(u.user_id)
    setLimitsForm(emptyLimitsForm)
  }

  const saveLimits = async (u: AdminUser) => {
    try {
      const body: UserLimits = {
        max_groups: toNullableNumber(limitsForm.max_groups),
        max_channels_total: toNullableNumber(limitsForm.max_channels_total),
        max_analyses_per_day: toNullableNumber(limitsForm.max_analyses_per_day),
        max_video_minutes: toNullableNumber(limitsForm.max_video_minutes),
        min_poll_interval_min: toNullableNumber(limitsForm.min_poll_interval_min),
        note: limitsForm.note.trim() === '' ? null : limitsForm.note,
      }
      await adminApi.putUserLimits(u.user_id, body)
      setEditingLimits(null)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const resetLimits = async (u: AdminUser) => {
    try {
      await adminApi.deleteUserLimits(u.user_id)
      setEditingLimits(null)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-8">
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

      <section className="space-y-3">
        {users.map((u) => (
          <div key={u.user_id} className="bg-white rounded-xl shadow-sm p-4 space-y-3">
            {/* 1행: 계정 식별 + 관리 버튼 */}
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <span className="font-medium text-gray-900 break-all">{u.email}</span>
                {u.display_name && <span className="text-sm text-gray-500">{u.display_name}</span>}
                <Badge tone={u.role === 'admin' ? 'amber' : 'gray'}>{u.role}</Badge>
                <Badge tone={u.status === 'active' ? 'green' : 'red'}>{u.status}</Badge>
                {u.usage?.has_override && <Badge tone="amber">한도조정</Badge>}
              </div>
              <div className="flex gap-1 shrink-0">
                <button onClick={() => toggleStatus(u)} className={actionBtn}>
                  {u.status === 'active' ? '정지' : '해제'}
                </button>
                <button onClick={() => issueTempPassword(u)} className={actionBtn}>
                  임시비번
                </button>
                <button
                  onClick={() => editingLimits === u.user_id ? setEditingLimits(null) : startEditLimits(u)}
                  className={actionBtn}
                >
                  한도편집
                </button>
              </div>
            </div>

            {/* 2행: 편집 컨트롤 (플랜·만료일) */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
              <label className="flex items-center gap-2">
                <span className="text-xs text-gray-500 whitespace-nowrap">플랜</span>
                <select
                  value={u.plan_id}
                  onChange={(e) => changePlan(u, Number(e.target.value))}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-sm"
                >
                  {plans.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2">
                <span className="text-xs text-gray-500 whitespace-nowrap">만료일 (UTC)</span>
                <input
                  type="datetime-local"
                  value={u.plan_expires_at ? u.plan_expires_at.slice(0, 16) : ''}
                  onChange={(e) => changeExpiry(u, e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-sm"
                />
                {u.plan_expires_at && (
                  <span className={`text-xs whitespace-nowrap ${expiryHint(u.plan_expires_at).className}`}>
                    {expiryHint(u.plan_expires_at).label}
                  </span>
                )}
              </label>
            </div>

            {/* 3행: 조회 전용 메타 */}
            <p className="text-xs text-gray-500">
              {u.usage
                ? `${u.usage.group_count}그룹 · ${u.usage.channel_count}채널 · 오늘 분석 ${u.usage.today_analyses}건`
                : '사용량 정보 없음'}
              {' · '}최근 로그인 {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : '-'}
            </p>

            {tempPw[u.user_id] && (
              <p className="text-xs bg-green-50 border border-green-200 rounded-lg px-2 py-1 break-all">
                임시 비밀번호: <code>{tempPw[u.user_id]}</code>
                <button
                  onClick={() => navigator.clipboard.writeText(tempPw[u.user_id])}
                  className="ml-2 text-blue-600 hover:underline"
                >
                  복사
                </button>
              </p>
            )}

            {editingLimits === u.user_id && (
              <div className="border-t border-gray-100 pt-3">
                <div className="flex flex-wrap gap-2 items-end">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">최대 그룹수</label>
                    <input
                      type="number"
                      value={limitsForm.max_groups}
                      onChange={(e) => setLimitsForm({ ...limitsForm, max_groups: e.target.value })}
                      placeholder="플랜값"
                      className="w-24 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">최대 채널수</label>
                    <input
                      type="number"
                      value={limitsForm.max_channels_total}
                      onChange={(e) => setLimitsForm({ ...limitsForm, max_channels_total: e.target.value })}
                      placeholder="플랜값"
                      className="w-24 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">일일 분석 한도</label>
                    <input
                      type="number"
                      value={limitsForm.max_analyses_per_day}
                      onChange={(e) => setLimitsForm({ ...limitsForm, max_analyses_per_day: e.target.value })}
                      placeholder="플랜값"
                      className="w-24 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">최대 영상 분(분)</label>
                    <input
                      type="number"
                      value={limitsForm.max_video_minutes}
                      onChange={(e) => setLimitsForm({ ...limitsForm, max_video_minutes: e.target.value })}
                      placeholder="플랜값"
                      className="w-24 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">최소 폴링 간격(분)</label>
                    <input
                      type="number"
                      value={limitsForm.min_poll_interval_min}
                      onChange={(e) => setLimitsForm({ ...limitsForm, min_poll_interval_min: e.target.value })}
                      placeholder="플랜값"
                      className="w-24 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <div className="flex-1 min-w-40">
                    <label className="block text-xs text-gray-500 mb-1">메모</label>
                    <input
                      value={limitsForm.note}
                      onChange={(e) => setLimitsForm({ ...limitsForm, note: e.target.value })}
                      className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
                    />
                  </div>
                  <button
                    onClick={() => saveLimits(u)}
                    className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700"
                  >
                    저장
                  </button>
                  <button
                    onClick={() => resetLimits(u)}
                    className="border border-gray-300 rounded-lg px-4 py-1.5 text-sm hover:bg-gray-50"
                  >
                    플랜값으로 초기화
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">초대</h2>
        <div className="bg-white rounded-xl shadow-sm p-4 space-y-3">
          <div className="flex flex-wrap gap-2 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">플랜</label>
              <select value={planSlug} onChange={(e) => setPlanSlug(e.target.value)}
                className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm">
                <option value="">기본 (free)</option>
                {plans.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
              </select>
            </div>
            <div className="flex-1 min-w-40">
              <label className="block text-xs text-gray-500 mb-1">메모 (초대 대상)</label>
              <input value={memo} onChange={(e) => setMemo(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm" />
            </div>
            <button onClick={createInvite}
              className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700">
              초대 링크 발급 (7일)
            </button>
          </div>
          {createdUrl && (
            <p className="text-xs bg-green-50 border border-green-200 rounded-lg px-3 py-2 break-all">
              발급됨: <code>{createdUrl}</code>
              <button onClick={() => navigator.clipboard.writeText(createdUrl)}
                className="ml-2 text-blue-600 hover:underline">복사</button>
            </p>
          )}
          <ul className="divide-y">
            {invites.map((i) => (
              <li key={i.invite_id} className="py-2 flex items-center justify-between text-sm">
                <span>
                  #{i.invite_id} {i.memo || '(메모 없음)'} · {planName(i.plan_id)} ·
                  만료 {new Date(i.expires_at).toLocaleDateString()} ·
                  {i.used_at ? ` 사용됨(user ${i.used_by})` : ' 미사용'}
                </span>
                {!i.used_at && (
                  <button onClick={() => revoke(i.invite_id)}
                    className="text-red-600 text-xs hover:underline">회수</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  )
}
