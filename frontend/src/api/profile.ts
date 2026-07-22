import { groupClient } from './http'
import type { DigestSection } from './types'

export interface RecordField {
  key: string
  label: string
  datatype: 'entity' | 'text' | 'number' | 'date'
  required: boolean
}

export interface RecordType {
  type_key: string
  label: string
  fields: RecordField[]
}

export interface RecordSchema {
  version: number
  types: RecordType[]
}

export interface VocabAxis {
  label: string
  values: string[]
  synonyms: Record<string, string>
}

export interface EnrichProposal {
  sections_add?: DigestSection[]
  record_fields_add?: { type_key: string; field: RecordField }[]
  vocab_add?: Record<string, VocabAxis>
  entity_attrs_add?: { entity: string; attrs: Record<string, string> }[]
  note?: string
  created_at?: string
}

export interface GroupProfile {
  persona: string
  digest_sections: DigestSection[]
  bootstrap_status: string
  bootstrap_at?: string
  record_schema?: RecordSchema
  vocab?: Record<string, VocabAxis>
  vocab_pending?: string[]
  enrich_proposal?: EnrichProposal
}

export interface ProfileUpdate {
  persona?: string
  digest_sections?: DigestSection[]
  record_schema?: RecordSchema
  vocab?: Record<string, VocabAxis>
}

export function profileApi(slug: string) {
  const c = groupClient(slug)
  return {
    get: () => c.get<GroupProfile>('/profile'),
    regenerate: () => c.post<GroupProfile>('/profile/regenerate'),
    put: (body: ProfileUpdate) => c.put<GroupProfile>('/profile', body),
    applyProposal: () => c.post<GroupProfile>('/profile/proposal/apply'),
    dismissProposal: () => c.post<GroupProfile>('/profile/proposal/dismiss'),
  }
}
