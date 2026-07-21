import { describe, it, expect } from 'vitest'
import { toRenderSections } from './DigestDetail'
import type { Digest } from '../api/types'

const base: Partial<Digest> = {
  digest_pk: 1, headline: 'H', period_start: '2026-07-01', period_end: '2026-07-08',
  video_count: 2, status: 'done', summary_md: null,
}

describe('toRenderSections', () => {
  it('uses digest_sections when present', () => {
    const d = { ...base, digest_sections: [
      { key: 'overview', kind: 'llm', title: '요약', body_md: '본문' },
    ] } as Digest
    const out = toRenderSections(d)
    expect(out[0].title).toBe('요약')
    expect(out[0].body_md).toBe('본문')
  })
  it('falls back to summary_md', () => {
    const d = { ...base, digest_sections: null, summary_md: '## 요약\n- 레거시' } as Digest
    const out = toRenderSections(d)
    expect(out).toHaveLength(1)
    expect(out[0].body_md).toContain('레거시')
  })
})
