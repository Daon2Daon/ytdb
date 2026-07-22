import { useState } from 'react'
import type { RecordField, RecordSchema } from '../api/profile'
import { addField, addType, removeField, removeType } from './DataProfile.logic'

const DATATYPES: RecordField['datatype'][] = ['entity', 'text', 'number', 'date']
const EMPTY_FIELD: RecordField = { key: '', label: '', datatype: 'text', required: false }

interface Props {
  schema: RecordSchema
  onChange: (s: RecordSchema) => void
}

export default function RecordSchemaBuilder({ schema, onChange }: Props) {
  const [newType, setNewType] = useState({ key: '', label: '' })
  const [drafts, setDrafts] = useState<Record<string, RecordField>>({})
  const draftFor = (tk: string): RecordField => drafts[tk] ?? EMPTY_FIELD
  const setDraft = (tk: string, patch: Partial<RecordField>) =>
    setDrafts({ ...drafts, [tk]: { ...draftFor(tk), ...patch } })

  return (
    <div className="space-y-4">
      {schema.types.map((t) => (
        <div key={t.type_key} className="border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-800">
              {t.label} <code className="text-xs text-gray-400">{t.type_key}</code>
            </span>
            <button type="button" onClick={() => onChange(removeType(schema, t.type_key))}
              className="text-xs text-red-400 hover:text-red-600">타입 삭제</button>
          </div>
          <ul className="space-y-1">
            {t.fields.map((f) => (
              <li key={f.key} className="flex items-center gap-2 text-sm text-gray-700">
                <span className="flex-1">{f.label} <code className="text-xs text-gray-400">{f.key}</code></span>
                <span className="text-xs text-gray-500">{f.datatype}{f.required ? ' · 필수' : ''}</span>
                <button type="button" onClick={() => onChange(removeField(schema, t.type_key, f.key))}
                  className="px-1 text-red-400 hover:text-red-600">×</button>
              </li>
            ))}
          </ul>
          {t.fields.length === 0 && (
            <p className="text-xs text-amber-600">필드를 1개 이상 추가해야 저장됩니다.</p>
          )}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input placeholder="key(영문)" value={draftFor(t.type_key).key}
              onChange={(e) => setDraft(t.type_key, { key: e.target.value })}
              className="border border-gray-200 rounded px-2 py-1 w-24" />
            <input placeholder="라벨" value={draftFor(t.type_key).label}
              onChange={(e) => setDraft(t.type_key, { label: e.target.value })}
              className="border border-gray-200 rounded px-2 py-1 w-24" />
            <select value={draftFor(t.type_key).datatype}
              onChange={(e) => setDraft(t.type_key, { datatype: e.target.value as RecordField['datatype'] })}
              className="border border-gray-200 rounded px-2 py-1">
              {DATATYPES.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
            <label className="flex items-center gap-1 text-gray-600">
              <input type="checkbox" checked={draftFor(t.type_key).required}
                onChange={(e) => setDraft(t.type_key, { required: e.target.checked })} />
              필수
            </label>
            <button type="button"
              onClick={() => {
                const d = draftFor(t.type_key)
                const next = addField(schema, t.type_key, { ...d, label: d.label || d.key })
                if (next !== schema) {
                  onChange(next)
                  setDrafts({ ...drafts, [t.type_key]: EMPTY_FIELD })
                }
              }}
              className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">필드 추가</button>
          </div>
        </div>
      ))}
      <div className="flex items-center gap-2 text-xs">
        <input placeholder="type_key(영문)" value={newType.key}
          onChange={(e) => setNewType({ ...newType, key: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <input placeholder="라벨" value={newType.label}
          onChange={(e) => setNewType({ ...newType, label: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <button type="button"
          onClick={() => {
            const next = addType(schema, newType.key, newType.label)
            if (next !== schema) { onChange(next); setNewType({ key: '', label: '' }) }
          }}
          className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">레코드 타입 추가</button>
      </div>
    </div>
  )
}
