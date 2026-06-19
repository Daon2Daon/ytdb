import { useMemo, useState } from 'react'
import type { SettingItem } from '../api/settings'
import type { DigestScheduleConfig } from '../api/types'

const MAX_CONFIGS = 10
const PERIOD_OPTIONS = [
  { value: 1, label: '1일 (일간)' },
  { value: 7, label: '7일 (주간)' },
  { value: 30, label: '30일 (월간)' },
]
const DAY_OPTIONS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function newConfig(index: number): DigestScheduleConfig {
  return {
    id: crypto.randomUUID(),
    name: `Digest ${index + 1}`,
    enabled: false,
    period_days: 7,
    schedule_time: '20:00',
    schedule_day: 'sun',
    schedule_dom: 1,
    timezone: 'Asia/Seoul',
    category: '',
    digest_prompt: '',
    telegram_enabled: false,
  }
}

function normalizePeriodDays(value: number): 1 | 7 | 30 {
  if (value === 1 || value === 30) return value
  return 7
}

function legacyFromItems(items: SettingItem[]): DigestScheduleConfig[] {
  const map: Record<string, string> = {}
  items.forEach((i) => { map[i.key] = i.value ?? '' })
  if (!map.enabled && !map.period_weeks && !map.schedule_day) return []
  return [{
    id: 'legacy',
    name: '주간 리뷰',
    enabled: String(map.enabled).toLowerCase() === 'true',
    period_days: 7,
    schedule_time: map.schedule_time || '20:00',
    schedule_day: DAY_OPTIONS.includes(map.schedule_day) ? map.schedule_day : 'sun',
    schedule_dom: 1,
    timezone: map.timezone || 'Asia/Seoul',
    category: map.category || '',
    digest_prompt: '',
    telegram_enabled: String(map.telegram_enabled).toLowerCase() === 'true',
  }]
}

function parseConfigs(raw: string | null | undefined, items: SettingItem[]): DigestScheduleConfig[] {
  if (raw) {
    try {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr) && arr.length) {
        return arr.map((item, i) => ({
          id: String(item.id || crypto.randomUUID()),
          name: String(item.name || `Digest ${i + 1}`),
          enabled: Boolean(item.enabled),
          period_days: normalizePeriodDays(Number(item.period_days)),
          schedule_time: String(item.schedule_time || '20:00'),
          schedule_day: DAY_OPTIONS.includes(item.schedule_day) ? item.schedule_day : 'sun',
          schedule_dom: Math.min(28, Math.max(1, Number(item.schedule_dom) || 1)),
          timezone: String(item.timezone || 'Asia/Seoul'),
          category: String(item.category || ''),
          digest_prompt: String(item.digest_prompt || ''),
          telegram_enabled: Boolean(item.telegram_enabled),
        }))
      }
    } catch {
      /* fall through */
    }
  }
  return legacyFromItems(items)
}

interface Props {
  items: SettingItem[]
  saving: boolean
  onSave: (items: SettingItem[]) => void
}

export default function DigestConfigsEditor({ items, saving, onSave }: Props) {
  const itemMap = useMemo(() => {
    const m: Record<string, SettingItem> = {}
    items.forEach((i) => (m[i.key] = i))
    return m
  }, [items])

  const [configs, setConfigs] = useState<DigestScheduleConfig[]>(() =>
    parseConfigs(itemMap.configs?.value, items),
  )
  const [shareLinkEnabled, setShareLinkEnabled] = useState(
    () => String(itemMap.share_link_enabled?.value ?? 'true').toLowerCase() === 'true',
  )

  const update = (index: number, patch: Partial<DigestScheduleConfig>) => {
    setConfigs((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)))
  }

  const remove = (index: number) => {
    setConfigs((prev) => prev.filter((_, i) => i !== index))
  }

  const add = () => {
    if (configs.length >= MAX_CONFIGS) return
    setConfigs((prev) => [...prev, newConfig(prev.length)])
  }

  const handleSave = () => {
    onSave([
      {
        key: 'configs',
        value: JSON.stringify(configs),
        value_type: 'json',
        is_secret: false,
      },
      {
        key: 'share_link_enabled',
        value: shareLinkEnabled ? 'true' : 'false',
        value_type: 'bool',
        is_secret: false,
      },
    ])
  }

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-5 max-w-2xl">
      <label className="flex items-center gap-2 text-sm cursor-pointer w-fit">
        <input
          type="checkbox"
          checked={shareLinkEnabled}
          onChange={(e) => setShareLinkEnabled(e.target.checked)}
        />
        <span className="font-medium text-gray-700">웹에서 자세히 보기 링크 첨부 (그룹 공통)</span>
      </label>
      <p className="text-xs text-gray-400">
        digest를 여러 개 등록할 수 있습니다. 활성 설정마다 독립적으로 생성·발송됩니다. LLM 비용은 설정 수에 비례합니다.
      </p>

      <div className="space-y-4">
        {configs.map((cfg, index) => (
          <div key={cfg.id} className="border border-gray-200 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <input
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm"
                value={cfg.name}
                onChange={(e) => update(index, { name: e.target.value })}
                placeholder="설정 이름"
              />
              <button
                type="button"
                onClick={() => remove(index)}
                className="px-2.5 py-1.5 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100 shrink-0"
              >
                삭제
              </button>
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer w-fit">
              <input
                type="checkbox"
                checked={cfg.enabled}
                onChange={(e) => update(index, { enabled: e.target.checked })}
              />
              <span className="text-gray-700">자동 생성 활성화</span>
            </label>

            <Field label="집계 기간">
              <select
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                value={cfg.period_days}
                onChange={(e) => update(index, { period_days: normalizePeriodDays(Number(e.target.value)) })}
              >
                {PERIOD_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>

            <Field label="실행 시각 (HH:MM)">
              <input
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                value={cfg.schedule_time}
                onChange={(e) => update(index, { schedule_time: e.target.value })}
              />
            </Field>

            {cfg.period_days === 7 && (
              <Field label="실행 요일">
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  value={cfg.schedule_day}
                  onChange={(e) => update(index, { schedule_day: e.target.value })}
                >
                  {DAY_OPTIONS.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </Field>
            )}

            {cfg.period_days === 30 && (
              <Field label="매월 실행일 (1–28)">
                <select
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                  value={cfg.schedule_dom}
                  onChange={(e) => update(index, { schedule_dom: Number(e.target.value) })}
                >
                  {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
                    <option key={d} value={d}>{d}일</option>
                  ))}
                </select>
              </Field>
            )}

            <Field label="시간대">
              <input
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                value={cfg.timezone}
                onChange={(e) => update(index, { timezone: e.target.value })}
              />
            </Field>

            <Field label="카테고리 필터 (선택)">
              <input
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                value={cfg.category}
                onChange={(e) => update(index, { category: e.target.value })}
              />
            </Field>

            <Field label="Digest 프롬프트 (비우면 그룹 기본값)">
              <textarea
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm min-h-[120px]"
                value={cfg.digest_prompt}
                onChange={(e) => update(index, { digest_prompt: e.target.value })}
              />
            </Field>

            <label className="flex items-center gap-2 text-sm cursor-pointer w-fit">
              <input
                type="checkbox"
                checked={cfg.telegram_enabled}
                onChange={(e) => update(index, { telegram_enabled: e.target.checked })}
              />
              <span className="text-gray-700">텔레그램 발송</span>
            </label>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={add}
        disabled={configs.length >= MAX_CONFIGS}
        className="px-3 py-2 text-sm rounded-lg border border-dashed border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
      >
        + digest 추가 ({configs.length}/{MAX_CONFIGS})
      </button>

      <div className="pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? '저장 중...' : '저장'}
        </button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-sm font-medium text-gray-700 mb-1">{label}</p>
      {children}
    </div>
  )
}
