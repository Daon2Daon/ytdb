import { useEffect, useMemo, useState } from 'react'
import type { FieldDef } from '../settings/defs'
import { initialValue, toSaveItem, type FormValue } from '../settings/convert'
import type { SettingItem } from '../api/settings'
import { meApi, type TelegramDestination } from '../api/me'
import TemplateBuilder, { type MessageTemplate } from './TemplateBuilder'

interface Props {
  defs: FieldDef[]
  items: SettingItem[]
  models?: string[]
  saving: boolean
  onSave: (items: SettingItem[]) => void
}

export default function SettingsForm({ defs, items, models = [], saving, onSave }: Props) {
  const itemMap = useMemo(() => {
    const m: Record<string, SettingItem> = {}
    items.forEach((i) => (m[i.key] = i))
    return m
  }, [items])

  const [form, setForm] = useState<Record<string, FormValue>>(() => {
    const init: Record<string, FormValue> = {}
    defs.forEach((d) => (init[d.key] = initialValue(d, itemMap[d.key])))
    return init
  })

  const set = (key: string, value: FormValue) => setForm((f) => ({ ...f, [key]: value }))
  const handleSave = () => onSave(defs.map((d) => toSaveItem(d, form[d.key])))

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-5 max-w-2xl">
      {defs.map((d) => {
        if (d.showIf) {
          const cur = form[d.showIf.key]
          if (cur !== d.showIf.equals) return null
        }
        return (
          <Field
            key={d.key}
            def={d}
            value={form[d.key]}
            isSet={Boolean(itemMap[d.key]?.value)}
            models={models}
            onChange={(v) => set(d.key, v)}
          />
        )
      })}
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

function Field({
  def, value, isSet, models, onChange,
}: {
  def: FieldDef
  value: FormValue
  isSet: boolean
  models: string[]
  onChange: (v: FormValue) => void
}) {
  const help = def.help && <p className="text-xs text-gray-400 mt-1">{def.help}</p>

  if (def.type === 'bool') {
    return (
      <div>
        <label className="flex items-center gap-2 text-sm cursor-pointer w-fit">
          <input type="checkbox" checked={value as boolean} onChange={(e) => onChange(e.target.checked)} />
          <span className="font-medium text-gray-700">{def.label}</span>
        </label>
        {help}
      </div>
    )
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{def.label}</label>
      {def.secret ? (
        <input
          type="password"
          value={value as string}
          placeholder={isSet ? '설정됨 (변경 시에만 입력)' : '미설정'}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ) : def.type === 'textarea' ? (
        <textarea
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          rows={10}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
          spellCheck={false}
        />
      ) : def.type === 'select' ? (
        <select
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {(!value || !(def.options ?? []).includes(value as string)) && <option value="">(미설정)</option>}
          {(def.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : def.type === 'model_select' ? (
        <select
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">(선택 안 됨)</option>
          {value && !models.includes(value as string) && (
            <option value={value as string}>{value as string} (현재값)</option>
          )}
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      ) : def.type === 'dest_select' ? (
        <DestSelect value={value as string} onChange={onChange} />
      ) : def.type === 'chatlist' ? (
        <ChatList value={value as string[]} onChange={onChange} />
      ) : def.type === 'time' ? (
        <input
          type="time"
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ) : def.type === 'timelist' ? (
        <TimeList value={value as string[]} onChange={onChange} />
      ) : def.type === 'template_builder' ? (
        <TemplateBuilder
          value={value as MessageTemplate}
          onChange={onChange}
        />
      ) : def.type === 'int_days' ? (
        <input type="number" min={0} value={value as string} onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      ) : def.type === 'int_hours' ? (
        <input type="number" min={0} value={value as string} onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      ) : (
        <input
          type={def.type === 'int' || def.type === 'float' ? 'number' : 'text'}
          step={def.type === 'float' ? '0.1' : undefined}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      )}
      {def.type === 'int_days' && <span className="ml-2 text-xs text-gray-400">일</span>}
      {def.type === 'int_hours' && <span className="ml-2 text-xs text-gray-400">시간</span>}
      {help}
    </div>
  )
}

function TimeList({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const [err, setErr] = useState('')
  const sorted = (arr: string[]) =>
    [...arr].sort((a, b) => {
      const m = (t: string) => {
        const [h, mm] = t.split(':').map(Number)
        return h * 60 + mm
      }
      return m(a) - m(b)
    })
  const add = () => {
    const t = draft.trim()
    if (!t) return
    if (!/^([01]?\d|2[0-3]):[0-5]\d$/.test(t)) { setErr('HH:MM 형식으로 입력하세요'); return }
    if (value.includes(t)) { setErr('이미 등록된 시각입니다'); return }
    if (value.length >= 10) { setErr('최대 10개까지 등록할 수 있습니다'); return }
    onChange(sorted([...value, t]))
    setDraft('')
    setErr('')
  }
  const remove = (t: string) => onChange(value.filter((x) => x !== t))
  return (
    <div className="space-y-2">
      {value.length === 0 ? (
        <p className="text-xs text-gray-400 italic">등록된 예약 시각이 없습니다.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {value.map((t) => (
            <span key={t} className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-50 border border-blue-200 rounded-full text-sm font-medium text-blue-700">
              {t}
              <button type="button" onClick={() => remove(t)} className="text-blue-400 hover:text-red-500">×</button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          type="time"
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm"
        />
        <button type="button" onClick={add} disabled={value.length >= 10} className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50">추가</button>
      </div>
      {err && <p className="text-xs text-red-500">{err}</p>}
    </div>
  )
}

function ChatList({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const rows = value.length ? value : ['']
  const update = (i: number, v: string) => {
    const next = [...rows]
    next[i] = v
    onChange(next)
  }
  const add = () => onChange([...rows, ''])
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i))
  return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2">
          <input
            value={r}
            placeholder="-100... 또는 사용자 ID"
            onChange={(e) => update(i, e.target.value)}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm"
          />
          <button type="button" onClick={() => remove(i)} className="px-2 text-red-500 hover:bg-red-50 rounded">×</button>
        </div>
      ))}
      <button type="button" onClick={add} className="text-sm text-blue-600 hover:underline">+ Chat ID 추가</button>
    </div>
  )
}

function DestSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [dests, setDests] = useState<TelegramDestination[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let alive = true
    meApi
      .telegramDestinations()
      .then((ds) => alive && setDests(ds))
      .catch(() => alive && setDests([]))
      .finally(() => alive && setLoaded(true))
    return () => {
      alive = false
    }
  }, [])

  // 현재 저장된 dest_id가 목록에 없으면(연결 해제 등) 선택값을 유지하기 위한 안내 옵션.
  const orphan = value && loaded && !dests.some((d) => String(d.dest_id) === value)

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      <option value="">미지정(자동)</option>
      {dests.map((d) => (
        <option key={d.dest_id} value={String(d.dest_id)}>
          {d.title ?? `연결 #${d.dest_id}`}
        </option>
      ))}
      {orphan && <option value={value}>연결 #{value} (현재값)</option>}
    </select>
  )
}
