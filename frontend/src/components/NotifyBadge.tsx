interface NotifyBadgeProps {
  analysisStatus: string
  notifiedAt: string | null
  notifySource: 'telegram' | 'web' | null
}

export default function NotifyBadge({ analysisStatus, notifiedAt, notifySource }: NotifyBadgeProps) {
  if (analysisStatus !== 'done') return null

  if (notifiedAt && notifySource === 'web') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
        웹 확인
      </span>
    )
  }

  if (notifiedAt) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
        {notifySource === 'telegram' ? 'Telegram 발송' : '발송 완료'}
      </span>
    )
  }

  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
      미발송
    </span>
  )
}
