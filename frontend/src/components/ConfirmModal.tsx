import type { ReactNode } from 'react'

interface ConfirmModalProps {
  title: string
  /** 본문 설명. 문자열 또는 임의 노드. */
  message?: ReactNode
  /** 메시지 아래에 강조해 보여줄 보조 내용(예: 대상 제목). */
  detail?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** 진행 중이면 버튼 비활성 + confirmLabel을 busyLabel로 대체. */
  busy?: boolean
  busyLabel?: string
  /** 위험 동작(빨강) 여부. 기본 true. */
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
}

/** 삭제·재분석 등 되돌리기 어려운 동작의 확인 모달. */
export default function ConfirmModal({
  title,
  message,
  detail,
  confirmLabel = '확인',
  cancelLabel = '취소',
  busy = false,
  busyLabel,
  danger = true,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  const confirmCls = danger
    ? 'bg-red-600 hover:bg-red-700'
    : 'bg-blue-600 hover:bg-blue-700'
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full space-y-4">
        <h3 className="font-bold text-gray-900">{title}</h3>
        {message && <div className="text-sm text-gray-600">{message}</div>}
        {detail && <div className="text-sm font-medium text-gray-800 line-clamp-3">{detail}</div>}
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`px-4 py-2 text-white rounded-lg text-sm disabled:opacity-50 ${confirmCls}`}
          >
            {busy ? busyLabel ?? confirmLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
