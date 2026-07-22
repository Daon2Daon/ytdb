import { describe, expect, it } from 'vitest'
import { computedToMarkdown } from './DigestDetail'
import type { DigestSection } from '../api/types'

describe('pivot section markdown', () => {
  it('renders entity_pivot items', () => {
    const s: DigestSection = {
      key: 'entity_pivot', kind: 'hybrid', title: '집중',
      data: { items: [{ entity: 'SoftBank', count: 2, samples: ['5G'] }] },
    }
    const md = computedToMarkdown(s)
    expect(md).toContain('SoftBank')
    expect(md).toContain('2건')
  })

  it('renders period_compare new/gone/continuing', () => {
    const s: DigestSection = {
      key: 'period_compare', kind: 'hybrid', title: '대비',
      data: {
        new: [{ entity: 'A', count: 1 }], gone: [],
        continuing: [{ entity: 'B', cur: 2, prev: 1 }],
      },
    }
    const md = computedToMarkdown(s)
    expect(md).toContain('신규: A')
    expect(md).toContain('지속: B (1→2건)')
  })

  it('renders top_records values', () => {
    const s: DigestSection = {
      key: 'top_records', kind: 'hybrid', title: '상위',
      data: { items: [{ entity: 'A', value: 1200, date: '2026-07-01' }] },
    }
    expect(computedToMarkdown(s)).toContain('A: 1200 · 2026-07-01')
  })

  it('empty hybrid returns empty string', () => {
    const s: DigestSection = { key: 'period_compare', kind: 'hybrid', title: '대비', data: {} }
    expect(computedToMarkdown(s)).toBe('')
  })
})
