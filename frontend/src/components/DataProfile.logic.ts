import type { EnrichProposal, RecordField, RecordSchema, VocabAxis } from '../api/profile'

export function addType(schema: RecordSchema, typeKey: string, label: string): RecordSchema {
  const key = typeKey.trim()
  if (!key || schema.types.some((t) => t.type_key === key)) return schema
  return { ...schema, types: [...schema.types, { type_key: key, label: label.trim() || key, fields: [] }] }
}

export function removeType(schema: RecordSchema, typeKey: string): RecordSchema {
  return { ...schema, types: schema.types.filter((t) => t.type_key !== typeKey) }
}

export function addField(schema: RecordSchema, typeKey: string, field: RecordField): RecordSchema {
  return {
    ...schema,
    types: schema.types.map((t) => {
      if (t.type_key !== typeKey) return t
      if (!field.key.trim() || t.fields.some((f) => f.key === field.key)) return t
      return { ...t, fields: [...t.fields, { ...field, key: field.key.trim() }] }
    }),
  }
}

export function removeField(schema: RecordSchema, typeKey: string, fieldKey: string): RecordSchema {
  return {
    ...schema,
    types: schema.types.map((t) =>
      t.type_key === typeKey ? { ...t, fields: t.fields.filter((f) => f.key !== fieldKey) } : t),
  }
}

export function parseValues(input: string): string[] {
  return [...new Set(input.split(',').map((s) => s.trim()).filter(Boolean))]
}

export function setAxisValues(
  vocab: Record<string, VocabAxis>, axis: string, input: string,
): Record<string, VocabAxis> {
  const cur = vocab[axis] ?? { label: axis, values: [], synonyms: {} }
  return { ...vocab, [axis]: { ...cur, values: parseValues(input) } }
}

export function addSynonym(
  vocab: Record<string, VocabAxis>, axis: string, from: string, to: string,
): Record<string, VocabAxis> {
  const f = from.trim()
  const t = to.trim()
  const cur = vocab[axis]
  if (!cur || !f || !t) return vocab
  return { ...vocab, [axis]: { ...cur, synonyms: { ...cur.synonyms, [f]: t } } }
}

export function removeSynonym(
  vocab: Record<string, VocabAxis>, axis: string, from: string,
): Record<string, VocabAxis> {
  const cur = vocab[axis]
  if (!cur) return vocab
  const next = { ...cur.synonyms }
  delete next[from]
  return { ...vocab, [axis]: { ...cur, synonyms: next } }
}

export function addAxis(
  vocab: Record<string, VocabAxis>, axis: string, label: string,
): Record<string, VocabAxis> {
  const key = axis.trim()
  if (!key || vocab[key]) return vocab
  return { ...vocab, [key]: { label: label.trim() || key, values: [], synonyms: {} } }
}

export function removeAxis(vocab: Record<string, VocabAxis>, axis: string): Record<string, VocabAxis> {
  const next = { ...vocab }
  delete next[axis]
  return next
}

export function proposalSummary(p: EnrichProposal | undefined): string[] {
  if (!p) return []
  const out: string[] = []
  p.sections_add?.forEach((s) => out.push(`섹션 추가: ${s.title}`))
  p.record_fields_add?.forEach((f) => out.push(`레코드 필드: ${f.type_key}.${f.field.label}`))
  Object.entries(p.vocab_add ?? {}).forEach(([axis, spec]) =>
    out.push(`어휘 확장: ${axis} (${spec.values.join(', ')})`))
  p.entity_attrs_add?.forEach((e) => out.push(`엔티티 속성: ${e.entity}`))
  return out
}
