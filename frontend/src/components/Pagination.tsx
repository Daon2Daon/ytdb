import { Fragment } from 'react'

interface Props {
  page: number
  pageSize: number
  total: number
  onChange: (page: number) => void
}

export default function Pagination({ page, pageSize, total, onChange }: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (totalPages <= 1) return null

  const pages = Array.from({ length: totalPages }, (_, i) => i + 1)
  const visible = pages.filter((p) => Math.abs(p - page) <= 2 || p === 1 || p === totalPages)

  return (
    <div className="flex items-center justify-center gap-1 mt-6">
      <button
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
      >
        이전
      </button>

      {visible.map((p, i) => {
        const prev = visible[i - 1]
        return (
          <Fragment key={p}>
            {prev && p - prev > 1 && (
              <span className="px-2 text-gray-400">…</span>
            )}
            <button
              onClick={() => onChange(p)}
              className={`px-3 py-1.5 rounded-lg text-sm border ${
                p === page
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'border-gray-200 hover:bg-gray-50'
              }`}
            >
              {p}
            </button>
          </Fragment>
        )
      })}

      <button
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
      >
        다음
      </button>
    </div>
  )
}
