import { useMemo, useState } from 'react'
import type { FieldDef } from '../settings/defs'
import { initialValue, toSaveItem, type FormValue } from '../settings/convert'
import type { SettingItem } from '../api/settings'

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
      {defs.map((d) => (
        <Field
          key={d.key}
          def={d}
          value={form[d.key]}
          isSet={Boolean(itemMap[d.key]?.value)}
          models={models}
          onChange={(v) => set(d.key, v)}
        />
      ))}
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
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value as boolean} onChange={(e) => onChange(e.target.checked)} />
        <span className="font-medium text-gray-700">{def.label}</span>
        {help}
      </label>
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
      ) : def.type === 'chatlist' ? (
        <ChatList value={value as string[]} onChange={onChange} />
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
