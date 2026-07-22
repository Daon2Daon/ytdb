import { groupClient } from './http'

export interface MergeCandidate {
  entity_pk: number
  canonical_name: string
  candidates: string[]
  mention_count: number
}

export function entitiesApi(slug: string) {
  const c = groupClient(slug)
  return {
    mergeCandidates: () => c.get<MergeCandidate[]>('/entities/merge-candidates'),
    approve: (pk: number, alias: string) =>
      c.post<{ merged: string[] }>(`/entities/${pk}/merge`, { alias }),
    reject: (pk: number, alias: string) =>
      c.post<{ rejected: string }>(`/entities/${pk}/reject`, { alias }),
  }
}
