
export interface MessageTemplate {
  fields: string[]
}

const PRESET_FULL: MessageTemplate = {
  fields: ['channel_name', 'headline', 'analysis_sections', 'bullet_points',
           'tags', 'published_at', 'duration', 'video_url', 'share_link'],
}

const PRESET_COMPACT: MessageTemplate = {
  fields: ['headline', 'one_line', 'short_summary_md',
           'sentiment', 'confidence_score', 'video_url', 'share_link'],
}

const ALL_FIELDS: { key: string; label: string }[] = [
  { key: 'channel_name',      label: '채널명' },
  { key: 'headline',          label: '헤드라인' },
  { key: 'one_line',          label: '한 줄 요약' },
  { key: 'short_summary_md',  label: '짧은 요약' },
  { key: 'analysis_sections', label: '상세 분석 본문' },
  { key: 'bullet_points',     label: '핵심 주장' },
  { key: 'key_points',        label: '핵심 포인트' },
  { key: 'insights',          label: '인사이트' },
  { key: 'entities',          label: '언급 개체' },
  { key: 'sentiment',         label: '감성' },
  { key: 'confidence_score',  label: '신뢰도 점수' },
  { key: 'published_at',      label: '게시일' },
  { key: 'duration',          label: '영상 길이' },
  { key: 'tags',              label: '태그' },
  { key: 'video_url',         label: '영상 링크' },
  { key: 'share_link',        label: '웹 공유 링크' },
]

const labelOf = (key: string) => ALL_FIELDS.find((f) => f.key === key)?.label ?? key

interface Props {
  value: MessageTemplate
  onChange: (v: MessageTemplate) => void
}

export default function TemplateBuilder({ value, onChange }: Props) {
  const included = value.fields
  const available = ALL_FIELDS.filter((f) => !included.includes(f.key))

  const move = (idx: number, dir: -1 | 1) => {
    const next = [...included]
    const target = idx + dir
    if (target < 0 || target >= next.length) return
    ;[next[idx], next[target]] = [next[target], next[idx]]
    onChange({ fields: next })
  }

  const remove = (key: string) => onChange({ fields: included.filter((k) => k !== key) })
  const add = (key: string) => onChange({ fields: [...included, key] })
  const applyPreset = (preset: MessageTemplate) => onChange({ fields: [...preset.fields] })

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => applyPreset(PRESET_FULL)}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Full 기본값으로
        </button>
        <button
          type="button"
          onClick={() => applyPreset(PRESET_COMPACT)}
          className="px-3 py-1 text-xs border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Compact 기본값으로
        </button>
      </div>

      {included.length > 0 && (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {included.map((key, idx) => (
            <div key={key} className="flex items-center gap-2 px-3 py-2 text-sm">
              <span className="flex-1 text-gray-700">{labelOf(key)}</span>
              <button
                type="button"
                onClick={() => move(idx, -1)}
                disabled={idx === 0}
                className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
              >▲</button>
              <button
                type="button"
                onClick={() => move(idx, 1)}
                disabled={idx === included.length - 1}
                className="px-1 text-gray-400 hover:text-gray-700 disabled:opacity-30"
              >▼</button>
              <button
                type="button"
                onClick={() => remove(key)}
                className="px-1 text-red-400 hover:text-red-600"
              >×</button>
            </div>
          ))}
        </div>
      )}

      {available.length > 0 && (
        <div className="border border-dashed border-gray-200 rounded-lg divide-y divide-gray-100">
          {available.map((f) => (
            <div key={f.key} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-400">
              <span className="flex-1">{f.label}</span>
              <button
                type="button"
                onClick={() => add(f.key)}
                className="px-2 py-0.5 text-xs text-blue-500 border border-blue-200 rounded hover:bg-blue-50"
              >+ 추가</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
