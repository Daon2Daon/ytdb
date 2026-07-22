import { groupClient } from './http'
import type { DigestSection } from './types'

export interface GroupProfile {
  persona: string
  digest_sections: DigestSection[]
  bootstrap_status: string
  bootstrap_at?: string
  record_schema?: { version: number; types: { type_key: string; label: string; fields: unknown[] }[] }
}

export function profileApi(slug: string) {
  const c = groupClient(slug)
  return {
    get: () => c.get<GroupProfile>('/profile'),
    regenerate: () => c.post<GroupProfile>('/profile/regenerate'),
  }
}
