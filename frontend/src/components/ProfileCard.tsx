import type { DigestSection } from '../api/types'

export function profileSummaryLine(sections: DigestSection[]): string {
  if (!sections.length) return '기본 구성'
  return sections.map((s) => s.title).join(' · ')
}

interface Props {
  sections: DigestSection[]
  status: string
  onRegenerate: () => void
  regenerating: boolean
}

export default function ProfileCard({ sections, status, onRegenerate, regenerating }: Props) {
  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-gray-800">이 그룹의 리포트 구성</h2>
        <button
          type="button" onClick={onRegenerate} disabled={regenerating}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
        >
          {regenerating ? '생성 중...' : '다시 생성'}
        </button>
      </div>
      <p className="text-sm text-gray-600">{profileSummaryLine(sections)}</p>
      {status === 'failed' && (
        <p className="text-xs text-amber-600">자동 구성에 실패해 기본 구성으로 동작 중입니다. '다시 생성'을 눌러보세요.</p>
      )}
    </div>
  )
}
