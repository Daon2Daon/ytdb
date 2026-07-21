import { describe, it, expect } from 'vitest'
import { COMPUTED_SECTION_DEFS, addSection, removeSection } from './DigestSectionBuilder'

describe('DigestSectionBuilder helpers', () => {
  it('exposes computed section catalog', () => {
    const keys = COMPUTED_SECTION_DEFS.map((d) => d.key)
    expect(keys).toContain('top_tags')
    expect(keys).toContain('top_viewed')
  })
  it('adds a computed section with title from catalog', () => {
    const out = addSection([], { key: 'top_tags', kind: 'computed' })
    expect(out).toHaveLength(1)
    expect(out[0].title).toBe('주요 태그')
  })
  it('removes a section by key', () => {
    const secs = [
      { key: 'overview', kind: 'llm' as const, title: '요약' },
      { key: 'top_tags', kind: 'computed' as const, title: '태그' },
    ]
    expect(removeSection(secs, 'overview').map((s) => s.key)).toEqual(['top_tags'])
  })
})
