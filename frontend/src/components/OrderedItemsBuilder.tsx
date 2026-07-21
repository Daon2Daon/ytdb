import type { ReactNode } from 'react'

export function moveItem<T>(items: T[], idx: number, dir: -1 | 1): T[] {
  const target = idx + dir
  if (target < 0 || target >= items.length) return items
  const next = [...items]
  ;[next[idx], next[target]] = [next[target], next[idx]]
  return next
}

export interface OrderedItem {
  key: string
  label: string
}

interface Props {
  included: OrderedItem[]
  available: OrderedItem[]
  onMove: (idx: number, dir: -1 | 1) => void
  onRemove: (key: string) => void
  onAdd: (key: string) => void
  renderExtra?: (key: string) => ReactNode
}

export default function OrderedItemsBuilder({
  included, available, onMove, onRemove, onAdd, renderExtra,
}: Props) {
  return (
    <div className="space-y-3">
      {included.length > 0 && (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {included.map((item, idx) => (
            <div key={item.key} className="px-3 py-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="flex-1 text-gray-700">{item.label}</span>
                <button type="button" onClick={() => onMove(idx, -1)} disabled={idx === 0}
                  className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30">▲</button>
                <button type="button" onClick={() => onMove(idx, 1)}
                  disabled={idx === included.length - 1}
                  className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30">▼</button>
                <button type="button" onClick={() => onRemove(item.key)}
                  className="px-1 text-red-400 hover:text-red-600">×</button>
              </div>
              {renderExtra?.(item.key)}
            </div>
          ))}
        </div>
      )}
      {available.length > 0 && (
        <div className="border border-dashed border-gray-200 rounded-lg divide-y divide-gray-100">
          {available.map((item) => (
            <div key={item.key} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-400">
              <span className="flex-1">{item.label}</span>
              <button type="button" onClick={() => onAdd(item.key)}
                className="px-2 py-0.5 text-xs text-blue-500 border border-blue-200 rounded hover:bg-blue-50">+ 추가</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
