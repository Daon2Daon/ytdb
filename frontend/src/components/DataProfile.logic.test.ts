import { describe, expect, it } from 'vitest'
import {
  addAxis, addField, addSynonym, addType, parseValues,
  proposalSummary, removeField, removeType, setAxisValues,
} from './DataProfile.logic'
import type { RecordSchema } from '../api/profile'

const schema: RecordSchema = {
  version: 1,
  types: [{ type_key: 'campaign', label: '캠페인', fields: [
    { key: 'entity', label: '브랜드', datatype: 'entity', required: true }] }],
}

describe('record schema edit', () => {
  it('addField appends unique key only', () => {
    const f = { key: 'region', label: '지역', datatype: 'text' as const, required: false }
    const out = addField(schema, 'campaign', f)
    expect(out.types[0].fields.map((x) => x.key)).toEqual(['entity', 'region'])
    expect(addField(out, 'campaign', f).types[0].fields).toHaveLength(2)
  })

  it('addType/removeType', () => {
    const out = addType(schema, 'topic', '주제')
    expect(out.types).toHaveLength(2)
    expect(removeType(out, 'campaign').types.map((t) => t.type_key)).toEqual(['topic'])
  })

  it('removeField', () => {
    expect(removeField(schema, 'campaign', 'entity').types[0].fields).toHaveLength(0)
  })
})

describe('vocab edit', () => {
  it('parseValues dedupes and trims', () => {
    expect(parseValues(' 긍정, 부정 ,긍정,')).toEqual(['긍정', '부정'])
  })

  it('setAxisValues + addSynonym', () => {
    let v = setAxisValues({}, 'sentiment', '긍정,부정')
    v = addSynonym(v, 'sentiment', 'positive', '긍정')
    expect(v.sentiment.values).toEqual(['긍정', '부정'])
    expect(v.sentiment.synonyms.positive).toBe('긍정')
  })

  it('addAxis ignores duplicates', () => {
    const v = addAxis(addAxis({}, 'a', 'A'), 'a', 'B')
    expect(v.a.label).toBe('A')
  })
})

describe('proposalSummary', () => {
  it('lists diff lines', () => {
    const lines = proposalSummary({
      sections_add: [{ key: 'risks', kind: 'llm', title: '리스크' }],
      record_fields_add: [{ type_key: 'campaign',
        field: { key: 'r', label: '지역', datatype: 'text', required: false } }],
      vocab_add: { sentiment: { label: '평가', values: ['중립'], synonyms: {} } },
      entity_attrs_add: [{ entity: 'SoftBank', attrs: { region: '일본' } }],
      note: 'n',
    })
    expect(lines).toHaveLength(4)
    expect(lines[0]).toContain('리스크')
  })
})
