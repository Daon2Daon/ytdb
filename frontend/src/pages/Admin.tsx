import { Fragment, useCallback, useEffect, useState } from 'react'
import { adminApi, type AdminUsageResponse, type AdminUser, type GlobalSettingItem, type Invite, type MigrateSchemasResponse, type PlanInfo, type UserLimits } from '../api/admin'

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

export default function Admin() {
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
  const [planEdits, setPlanEdits] = useState<Record<number, Partial<PlanInfo>>>({})
  const [usage, setUsage] = useState<AdminUsageResponse | null>(null)
  const [usageWindow, setUsageWindow] = useState('this_month')
  const [usageError, setUsageError] = useState<string | null>(null)
  const [migrating, setMigrating] = useState(false)
  const [migration, setMigration] = useState<MigrateSchemasResponse | null>(null)
  const [migrationError, setMigrationError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [u, p, i] = await Promise.all([adminApi.users(), adminApi.plans(), adminApi.invites()])
      setUsers(u); setPlans(p); setInvites(i); setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const loadUsage = useCallback(async () => {
    try {
      setUsage(await adminApi.usage(usageWindow))
      setUsageError(null)
    } catch (e) {
      setUsageError((e as Error).message)
    }
  }, [usageWindow])

  useEffect(() => { loadUsage() }, [loadUsage])

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

  const planEdit = (p: PlanInfo) => planEdits[p.plan_id] ?? {}

  const setPlanField = (p: PlanInfo, field: keyof PlanInfo, value: string) => {
    setPlanEdits((prev) => ({
      ...prev,
      [p.plan_id]: {
        ...prev[p.plan_id],
        [field]: field === 'name' ? value : Number(value),
      },
    }))
  }

  const savePlan = async (p: PlanInfo) => {
    try {
      const edit = planEdit(p)
      await adminApi.patchPlan(p.plan_id, {
        name: edit.name ?? p.name,
        max_groups: edit.max_groups ?? p.max_groups,
        max_channels_total: edit.max_channels_total ?? p.max_channels_total,
        max_analyses_per_day: edit.max_analyses_per_day ?? p.max_analyses_per_day,
        max_video_minutes: edit.max_video_minutes ?? p.max_video_minutes,
        min_poll_interval_min: edit.min_poll_interval_min ?? p.min_poll_interval_min,
      })
      setPlanEdits((prev) => {
        const next = { ...prev }
        delete next[p.plan_id]
        return next
      })
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const runMigration = async () => {
    if (migrating) return
    setMigrating(true)
    setMigrationError(null)
    try {
      setMigration(await adminApi.migrateSchemas())
    } catch (e) {
      setMigrationError(e instanceof Error ? e.message : '실행 실패')
    } finally {
      setMigrating(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">관리자</h1>
        <a href="/" className="text-sm text-blue-600 hover:underline">← 앱으로</a>
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">사용자</h2>
        <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-3 py-2">이메일</th><th className="px-3 py-2">이름</th>
                <th className="px-3 py-2">역할</th><th className="px-3 py-2">상태</th>
                <th className="px-3 py-2">플랜</th><th className="px-3 py-2">만료일 (UTC)</th>
                <th className="px-3 py-2">최근 로그인</th>
                <th className="px-3 py-2">사용량</th><th className="px-3 py-2">관리</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <Fragment key={u.user_id}>
                  <tr className="border-b last:border-0">
                    <td className="px-3 py-2">{u.email}</td>
                    <td className="px-3 py-2">{u.display_name || '-'}</td>
                    <td className="px-3 py-2">{u.role}</td>
                    <td className="px-3 py-2">{u.status}</td>
                    <td className="px-3 py-2">
                      <select
                        value={u.plan_id}
                        onChange={(e) => changePlan(u, Number(e.target.value))}
                        className="border border-gray-300 rounded-lg px-2 py-1 text-xs"
                      >
                        {plans.map((p) => <option key={p.plan_id} value={p.plan_id}>{p.name}</option>)}
                      </select>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <input
                          type="datetime-local"
                          value={u.plan_expires_at ? u.plan_expires_at.slice(0, 16) : ''}
                          onChange={(e) => changeExpiry(u, e.target.value)}
                          className="border border-gray-300 rounded-lg px-2 py-1 text-xs"
                        />
                        {u.plan_expires_at && (
                          <span className={`text-xs ${expiryHint(u.plan_expires_at).className}`}>
                            {expiryHint(u.plan_expires_at).label}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-gray-400">
                      {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : '-'}
                    </td>
                    <td className="px-3 py-2 text-xs text-gray-600">
                      {u.usage
                        ? `${u.usage.group_count}그룹 · ${u.usage.channel_count}채널 · 오늘 ${u.usage.today_analyses}건`
                        : '-'}
                      {u.usage?.has_override && (
                        <span className="ml-1 text-xs text-amber-600">한도조정</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        <button
                          onClick={() => toggleStatus(u)}
                          className="border border-gray-300 rounded-lg px-2 py-1 text-xs hover:bg-gray-50"
                        >
                          {u.status === 'active' ? '정지' : '해제'}
                        </button>
                        <button
                          onClick={() => issueTempPassword(u)}
                          className="border border-gray-300 rounded-lg px-2 py-1 text-xs hover:bg-gray-50"
                        >
                          임시비번
                        </button>
                        <button
                          onClick={() => editingLimits === u.user_id ? setEditingLimits(null) : startEditLimits(u)}
                          className="border border-gray-300 rounded-lg px-2 py-1 text-xs hover:bg-gray-50"
                        >
                          한도편집
                        </button>
                      </div>
                      {tempPw[u.user_id] && (
                        <p className="text-xs bg-green-50 border border-green-200 rounded-lg px-2 py-1 mt-1 break-all">
                          임시 비밀번호: <code>{tempPw[u.user_id]}</code>
                          <button
                            onClick={() => navigator.clipboard.writeText(tempPw[u.user_id])}
                            className="ml-2 text-blue-600 hover:underline"
                          >
                            복사
                          </button>
                        </p>
                      )}
                    </td>
                  </tr>
                  {editingLimits === u.user_id && (
                    <tr className="border-b bg-gray-50">
                      <td colSpan={9} className="px-3 py-3">
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
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">플랜</h2>
        <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-3 py-2">슬러그</th><th className="px-3 py-2">이름</th>
                <th className="px-3 py-2">최대 그룹수</th><th className="px-3 py-2">최대 채널수</th>
                <th className="px-3 py-2">일일 분석 한도</th><th className="px-3 py-2">최대 영상 분</th>
                <th className="px-3 py-2">최소 폴링 간격</th><th className="px-3 py-2">기본</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={p.plan_id} className="border-b last:border-0">
                  <td className="px-3 py-2 text-gray-400">{p.slug}</td>
                  <td className="px-3 py-2">
                    <input
                      value={planEdit(p).name ?? p.name}
                      onChange={(e) => setPlanField(p, 'name', e.target.value)}
                      className="w-28 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      value={planEdit(p).max_groups ?? p.max_groups}
                      onChange={(e) => setPlanField(p, 'max_groups', e.target.value)}
                      className="w-20 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      value={planEdit(p).max_channels_total ?? p.max_channels_total}
                      onChange={(e) => setPlanField(p, 'max_channels_total', e.target.value)}
                      className="w-20 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      value={planEdit(p).max_analyses_per_day ?? p.max_analyses_per_day}
                      onChange={(e) => setPlanField(p, 'max_analyses_per_day', e.target.value)}
                      className="w-20 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      value={planEdit(p).max_video_minutes ?? p.max_video_minutes}
                      onChange={(e) => setPlanField(p, 'max_video_minutes', e.target.value)}
                      className="w-20 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="number"
                      value={planEdit(p).min_poll_interval_min ?? p.min_poll_interval_min}
                      onChange={(e) => setPlanField(p, 'min_poll_interval_min', e.target.value)}
                      className="w-20 border border-gray-300 rounded-lg px-2 py-1 text-sm"
                    />
                  </td>
                  <td className="px-3 py-2 text-gray-400">{p.is_default ? '예' : '-'}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => savePlan(p)}
                      className="bg-blue-600 text-white rounded-lg px-3 py-1 text-xs hover:bg-blue-700"
                    >
                      저장
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">AI 사용량</h2>
          <select
            value={usageWindow}
            onChange={(e) => setUsageWindow(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
          >
            <option value="this_month">이번 달</option>
            <option value="last_month">지난달</option>
            <option value="30d">최근 30일</option>
          </select>
        </div>
        {usageError && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{usageError}</p>}
        {usage && usage.null_cost_row_count > 0 && (
          <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            단가 미등록 호출 {usage.null_cost_row_count}건 — 전역설정의 ai_model_prices에 단가를 등록하세요.
          </p>
        )}
        {usage?.youtube && (
          <div className="bg-white rounded-xl shadow-sm p-4 space-y-2">
            <h3 className="text-sm font-semibold text-gray-800">
              YouTube 쿼터 (PT {usage.youtube.usage_date} · 한도 {usage.youtube.daily_quota.toLocaleString()})
            </h3>
            {usage.youtube.entries.length === 0 ? (
              <p className="text-sm text-gray-400">오늘 기록된 호출이 없습니다.</p>
            ) : (
              <ul className="divide-y">
                {usage.youtube.entries.map((e) => (
                  <li key={e.key_fp} className="py-1.5 flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2">
                      <code className="font-mono text-xs text-gray-600">{e.key_fp}</code>
                      {e.is_system_key && (
                        <span className="text-xs text-amber-600 border border-amber-200 bg-amber-50 rounded px-1">시스템 키</span>
                      )}
                    </span>
                    <span
                      className={
                        e.pct >= 100
                          ? 'text-red-600 font-bold'
                          : e.pct >= 80
                            ? 'text-amber-600 font-bold'
                            : 'text-gray-600'
                      }
                    >
                      {e.units.toLocaleString()} 유닛 ({e.pct}%)
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="bg-white rounded-xl shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-3 py-2">사용자</th><th className="px-3 py-2">모델</th>
                <th className="px-3 py-2">purpose</th><th className="px-3 py-2 text-right">호출</th>
                <th className="px-3 py-2 text-right">입력 토큰</th><th className="px-3 py-2 text-right">출력 토큰</th>
                <th className="px-3 py-2 text-right">비용</th>
              </tr>
            </thead>
            <tbody>
              {usage && usage.rows.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-gray-400">사용 내역이 없습니다.</td></tr>
              )}
              {usage?.rows.map((r, idx) => (
                <tr key={idx} className="border-b last:border-0">
                  <td className="px-3 py-2">{r.user_id == null ? '시스템' : (r.email ?? `user ${r.user_id}`)}</td>
                  <td className="px-3 py-2">{r.model}</td>
                  <td className="px-3 py-2">{r.purpose}</td>
                  <td className="px-3 py-2 text-right">{r.calls.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{r.input_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{r.output_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right">{r.cost_usd == null ? '—' : `$${r.cost_usd.toFixed(4)}`}</td>
                </tr>
              ))}
            </tbody>
            {usage && (
              <tfoot>
                <tr className="border-t font-medium">
                  <td className="px-3 py-2" colSpan={6}>총 비용</td>
                  <td className="px-3 py-2 text-right">${usage.total_cost_usd.toFixed(4)}</td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </section>

      <GlobalSettingsSection />

      <section className="space-y-3">
        <h2 className="font-semibold text-gray-800">시스템 도구</h2>
        <div className="bg-white rounded-xl shadow-sm p-4 space-y-3">
          <button
            onClick={runMigration}
            disabled={migrating}
            className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {migrating ? '실행 중…' : '전 스키마 마이그레이션 실행'}
          </button>
          {migrationError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{migrationError}</p>
          )}
          {migration && (
            <>
              <p className="text-sm text-gray-700">
                성공 {migration.summary.ok} · 실패 {migration.summary.failed} · 스킵 {migration.summary.skipped}
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-gray-500 border-b">
                      <th className="px-3 py-2">그룹</th><th className="px-3 py-2">스키마</th>
                      <th className="px-3 py-2">상태</th><th className="px-3 py-2 text-right">소요(ms)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {migration.results.map((r) => (
                      <tr key={r.group_id} className="border-b last:border-0">
                        <td className="px-3 py-2">{r.slug}</td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-600">{r.schema_name}</td>
                        <td className="px-3 py-2">
                          {r.status === 'ok' ? (
                            <span className="text-green-600">ok</span>
                          ) : r.status === 'failed' ? (
                            <span className="text-red-600">failed{r.error ? ` — ${r.error}` : ''}</span>
                          ) : (
                            <span className="text-gray-400">skipped</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right">{r.duration_ms.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

// 키 순서·구성은 서버 _GLOBAL_KEYS가 단일 출처 — 여기는 표시 라벨만 담당.
const GLOBAL_SETTING_LABELS: Record<string, { label: string; help?: string }> = {
  youtube_api_key: { label: '시스템 YouTube API 키' },
  central_poll_floor_min: { label: '중앙 폴링 하한(분)' },
  youtube_daily_quota: { label: 'YouTube 일일 쿼터' },
  ai_base_url: { label: 'AI 게이트웨이 Base URL' },
  ai_api_key: { label: 'AI 게이트웨이 API 키' },
  ai_primary_model: { label: 'AI 기본 모델' },
  ai_digest_model: { label: 'AI 다이제스트 모델' },
  ai_model_prices: { label: 'AI 모델 단가표(JSON)', help: '{"모델prefix": {"input": n, "output": n}} — $/1M 토큰' },
  telegram_bot_token: { label: '공용 텔레그램 봇 토큰' },
  db_host: { label: '기본 DB 호스트', help: '사용자 그룹 데이터 평면 폴백 DSN — 그룹에 자체 DB 설정이 없으면 이 접속을 사용' },
  db_port: { label: '기본 DB 포트' },
  db_name: { label: '기본 DB 이름' },
  db_username: { label: '기본 DB 사용자' },
  db_password: { label: '기본 DB 비밀번호' },
  db_sslmode: { label: '기본 DB sslmode' },
}

function GlobalSettingsSection() {
  const [items, setItems] = useState<GlobalSettingItem[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await adminApi.globalSettings()
      setItems(list)
      setValues(Object.fromEntries(list.map((i) => [i.key, i.value])))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      // 빈 값은 서버가 무시(클리어 불가)하므로 전송 자체를 생략한다.
      const payload = items
        .map((i) => ({ ...i, value: (values[i.key] ?? '').trim() }))
        .filter((i) => i.value !== '')
      const updated = await adminApi.putGlobalSettings(payload)
      setItems(updated)
      setValues(Object.fromEntries(updated.map((i) => [i.key, i.value])))
      setSavedAt(new Date().toLocaleTimeString('ko-KR'))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-gray-800">전역 설정</h2>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
      <div className="bg-white rounded-xl shadow-sm p-4 space-y-4">
        <p className="text-xs text-gray-400">
          일반 사용자 그룹에 적용되는 기본값입니다. 시크릿은 마스킹되어 표시되며, 그대로 두고 저장하면 변경되지 않습니다.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {items.map((i) => {
            const meta = GLOBAL_SETTING_LABELS[i.key] ?? { label: i.key }
            return (
              <div key={i.key} className={i.key === 'ai_model_prices' ? 'sm:col-span-2' : ''}>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {meta.label} <span className="font-normal text-gray-400 text-xs font-mono">{i.key}</span>
                </label>
                {i.key === 'ai_model_prices' ? (
                  <textarea
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    rows={3}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="미설정"
                  />
                ) : i.key === 'db_sslmode' ? (
                  <select
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">(미설정 — prefer 적용)</option>
                    <option value="disable">disable</option>
                    <option value="prefer">prefer</option>
                    <option value="require">require</option>
                  </select>
                ) : (
                  <input
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="미설정"
                  />
                )}
                {meta.help && <p className="text-xs text-gray-400 mt-1">{meta.help}</p>}
              </div>
            )
          })}
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? '저장 중…' : '저장'}
        </button>
      </div>
    </section>
  )
}
