interface NotifyBadgeProps {
  analysisStatus: string
  notifiedAt: string | null
}

export default function NotifyBadge({ analysisStatus, notifiedAt }: NotifyBadgeProps) {
  if (analysisStatus !== 'done') return null

  if (notifiedAt) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
        발송 완료
      </span>
    )
  }

  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
      미발송
    </span>
  )
}
