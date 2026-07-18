import { useCallback, useEffect, useState } from 'react'
import { adminApi, type PlanInfo } from '../../api/admin'

/** 플랜 한도 편집. */
export default function PlansTab() {
  const [plans, setPlans] = useState<PlanInfo[]>([])
  const [planEdits, setPlanEdits] = useState<Record<number, Partial<PlanInfo>>>({})
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPlans(await adminApi.plans())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

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

  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
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
    </div>
  )
}
