import { describe, expect, it } from 'vitest'
import { addSection, PIVOT_SECTION_DEFS, setSectionParam } from './DigestSectionBuilder'

describe('pivot section add', () => {
  it('adds hybrid section with default record_type', () => {
    const out = addSection([], { key: 'entity_pivot', kind: 'hybrid' }, ['campaign', 'topic'])
    expect(out[0].kind).toBe('hybrid')
    expect(out[0].params?.record_type).toBe('campaign')
  })

  it('setSectionParam updates record_type', () => {
    const secs = addSection([], { key: 'top_records', kind: 'hybrid' }, ['a', 'b'])
    const out = setSectionParam(secs, 'top_records', 'record_type', 'b')
    expect(out[0].params?.record_type).toBe('b')
  })

  it('pivot defs cover three keys', () => {
    expect(PIVOT_SECTION_DEFS.map((p) => p.key))
      .toEqual(['entity_pivot', 'period_compare', 'top_records'])
  })
})
