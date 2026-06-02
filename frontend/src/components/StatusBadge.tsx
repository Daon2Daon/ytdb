type Status = 'pending' | 'processing' | 'done' | 'failed' | string

const MAP: Record<string, { label: string; cls: string }> = {
  pending: { label: '대기', cls: 'bg-yellow-100 text-yellow-700' },
  processing: { label: '분석 중', cls: 'bg-blue-100 text-blue-700 animate-pulse' },
  done: { label: '완료', cls: 'bg-green-100 text-green-700' },
  failed: { label: '실패', cls: 'bg-red-100 text-red-700' },
}

export default function StatusBadge({ status }: { status: Status }) {
  const info = MAP[status] ?? { label: status, cls: 'bg-gray-100 text-gray-600' }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${info.cls}`}>
      {info.label}
    </span>
  )
}
