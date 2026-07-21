import { describe, it, expect } from 'vitest'
import { profileSummaryLine } from './ProfileCard'

describe('profileSummaryLine', () => {
  it('summarizes section titles', () => {
    const line = profileSummaryLine([
      { key: 'overview', kind: 'llm', title: '핵심 요약' },
      { key: 'top_tags', kind: 'computed', title: '주요 태그' },
    ])
    expect(line).toBe('핵심 요약 · 주요 태그')
  })
  it('handles empty', () => {
    expect(profileSummaryLine([])).toBe('기본 구성')
  })
})
