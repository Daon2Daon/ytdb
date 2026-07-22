import { useState } from 'react'
import type { VocabAxis } from '../api/profile'
import { addAxis, addSynonym, removeAxis, removeSynonym, setAxisValues } from './DataProfile.logic'

interface Props {
  vocab: Record<string, VocabAxis>
  onChange: (v: Record<string, VocabAxis>) => void
}

export default function VocabEditor({ vocab, onChange }: Props) {
  const [newAxis, setNewAxis] = useState({ key: '', label: '' })
  const [synDrafts, setSynDrafts] = useState<Record<string, { from: string; to: string }>>({})

  return (
    <div className="space-y-4">
      {Object.entries(vocab).map(([axis, spec]) => {
        const draft = synDrafts[axis] ?? { from: '', to: '' }
        return (
          <div key={axis} className="border border-gray-200 rounded-lg p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-800">
                {spec.label} <code className="text-xs text-gray-400">{axis}</code>
              </span>
              <button type="button" onClick={() => onChange(removeAxis(vocab, axis))}
                className="text-xs text-red-400 hover:text-red-600">축 삭제</button>
            </div>
            <input
              className="w-full border border-gray-200 rounded px-2 py-1 text-sm"
              placeholder="표준 값 (쉼표 구분)"
              value={spec.values.join(', ')}
              onChange={(e) => onChange(setAxisValues(vocab, axis, e.target.value))}
            />
            <ul className="space-y-1 text-xs text-gray-600">
              {Object.entries(spec.synonyms).map(([from, to]) => (
                <li key={from} className="flex items-center gap-2">
                  <span className="flex-1">{from} → {to}</span>
                  <button type="button" onClick={() => onChange(removeSynonym(vocab, axis, from))}
                    className="px-1 text-red-400 hover:text-red-600">×</button>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2 text-xs">
              <input placeholder="동의어" value={draft.from}
                onChange={(e) => setSynDrafts({ ...synDrafts, [axis]: { ...draft, from: e.target.value } })}
                className="border border-gray-200 rounded px-2 py-1 w-24" />
              <span>→</span>
              <input placeholder="표준 값" value={draft.to}
                onChange={(e) => setSynDrafts({ ...synDrafts, [axis]: { ...draft, to: e.target.value } })}
                className="border border-gray-200 rounded px-2 py-1 w-24" />
              <button type="button"
                onClick={() => {
                  onChange(addSynonym(vocab, axis, draft.from, draft.to))
                  setSynDrafts({ ...synDrafts, [axis]: { from: '', to: '' } })
                }}
                className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">추가</button>
            </div>
          </div>
        )
      })}
      <div className="flex items-center gap-2 text-xs">
        <input placeholder="축 key(영문)" value={newAxis.key}
          onChange={(e) => setNewAxis({ ...newAxis, key: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <input placeholder="라벨" value={newAxis.label}
          onChange={(e) => setNewAxis({ ...newAxis, label: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 w-28" />
        <button type="button"
          onClick={() => {
            const next = addAxis(vocab, newAxis.key, newAxis.label)
            if (next !== vocab) { onChange(next); setNewAxis({ key: '', label: '' }) }
          }}
          className="px-2 py-1 border border-gray-300 rounded hover:bg-gray-50">어휘 축 추가</button>
      </div>
    </div>
  )
}
