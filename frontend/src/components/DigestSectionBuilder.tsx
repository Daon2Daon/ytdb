import type { DigestSection } from '../api/types'
import OrderedItemsBuilder, { moveItem } from './OrderedItemsBuilder'

export const COMPUTED_SECTION_DEFS: { key: string; title: string }[] = [
  { key: 'stats_overview', title: '이번 기간 개요' },
  { key: 'sentiment_breakdown', title: '평가 분포' },
  { key: 'top_tags', title: '주요 태그' },
  { key: 'top_channels', title: '주요 채널' },
  { key: 'top_viewed', title: '조회수 상위' },
]

const LLM_PRESETS: { key: string; title: string; guide: string }[] = [
  { key: 'overview', title: '핵심 요약', guide: '이번 기간을 가로지르는 3~5개 핵심 흐름을 서술' },
  { key: 'perspectives', title: '관점 비교', guide: '합의된 관점과 엇갈리는 관점을 구분해 대비' },
  { key: 'insights', title: '핵심 인사이트', guide: '시청자가 실제 판단에 쓸 수 있는 구체적 인사이트' },
]

export const PIVOT_SECTION_DEFS: { key: string; title: string }[] = [
  { key: 'entity_pivot', title: '엔티티 집중 분석' },
  { key: 'period_compare', title: '지난 기간 대비' },
  { key: 'top_records', title: '수치 상위' },
]

const ALL_ADDABLE = [
  ...LLM_PRESETS.map((p) => ({ key: p.key, label: `${p.title} (LLM)` })),
  ...COMPUTED_SECTION_DEFS.map((c) => ({ key: c.key, label: `${c.title} (자동)` })),
]

export function addSection(
  sections: DigestSection[],
  add: { key: string; kind: 'llm' | 'computed' | 'hybrid' },
  recordTypes: string[] = [],
): DigestSection[] {
  if (add.kind === 'hybrid') {
    const def = PIVOT_SECTION_DEFS.find((d) => d.key === add.key)
    return [...sections, {
      key: add.key, kind: 'hybrid', title: def?.title ?? add.key,
      params: recordTypes.length ? { record_type: recordTypes[0] } : {},
    }]
  }
  if (add.kind === 'computed') {
    const def = COMPUTED_SECTION_DEFS.find((d) => d.key === add.key)
    return [...sections, { key: add.key, kind: 'computed', title: def?.title ?? add.key }]
  }
  const preset = LLM_PRESETS.find((p) => p.key === add.key)
  return [...sections, {
    key: add.key, kind: 'llm', title: preset?.title ?? add.key, guide: preset?.guide ?? '',
  }]
}

export function setSectionParam(
  sections: DigestSection[], key: string, name: string, value: string,
): DigestSection[] {
  return sections.map((s) =>
    s.key === key ? { ...s, params: { ...(s.params ?? {}), [name]: value } } : s)
}

export function removeSection(sections: DigestSection[], key: string): DigestSection[] {
  return sections.filter((s) => s.key !== key)
}

interface Props {
  sections: DigestSection[]
  onChange: (s: DigestSection[]) => void
  recordTypes?: string[]
}

export default function DigestSectionBuilder({ sections, onChange, recordTypes = [] }: Props) {
  const addable = [
    ...ALL_ADDABLE,
    ...(recordTypes.length
      ? PIVOT_SECTION_DEFS.map((p) => ({ key: p.key, label: `${p.title} (레코드)` }))
      : []),
  ]
  const includedKeys = new Set(sections.map((s) => s.key))
  const available = addable.filter((a) => !includedKeys.has(a.key))
  const kindOf = (key: string): 'llm' | 'computed' | 'hybrid' =>
    PIVOT_SECTION_DEFS.some((p) => p.key === key) ? 'hybrid'
      : COMPUTED_SECTION_DEFS.some((c) => c.key === key) ? 'computed' : 'llm'

  return (
    <OrderedItemsBuilder
      included={sections.map((s) => ({
        key: s.key,
        label: `${s.title}${s.kind === 'computed' ? ' (자동)' : s.kind === 'hybrid' ? ' (레코드)' : ''}`,
      }))}
      available={available}
      onMove={(idx, dir) => onChange(moveItem(sections, idx, dir))}
      onRemove={(key) => onChange(removeSection(sections, key))}
      onAdd={(key) => onChange(addSection(sections, { key, kind: kindOf(key) }, recordTypes))}
      renderExtra={(key) => {
        const s = sections.find((x) => x.key === key)
        if (!s) return null
        const idx = sections.findIndex((x) => x.key === key)
        if (s.kind === 'llm') {
          return (
            <input
              className="mt-1 w-full border border-gray-200 rounded px-2 py-1 text-xs text-gray-600"
              placeholder="작성 지침 (선택)"
              value={s.guide ?? ''}
              onChange={(e) => {
                const next = [...sections]
                next[idx] = { ...s, guide: e.target.value }
                onChange(next)
              }}
            />
          )
        }
        if (s.kind === 'hybrid' && recordTypes.length > 1) {
          return (
            <select
              className="mt-1 border border-gray-200 rounded px-2 py-1 text-xs text-gray-600"
              value={(s.params?.record_type as string) ?? recordTypes[0]}
              onChange={(e) => onChange(setSectionParam(sections, key, 'record_type', e.target.value))}
            >
              {recordTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )
        }
        return null
      }}
    />
  )
}
