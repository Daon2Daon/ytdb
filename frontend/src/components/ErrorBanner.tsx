interface Props {
  message: string
  onRetry?: () => void
}

export default function ErrorBanner({ message, onRetry }: Props) {
  return (
    <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 flex items-start gap-3 text-red-700 text-sm">
      <span className="text-lg leading-none">⚠️</span>
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="underline hover:no-underline shrink-0">
          다시 시도
        </button>
      )}
    </div>
  )
}
