import { rootApi } from './http'
import type { Group } from './types'

export const groupApi = {
  list: () => rootApi.get<Group[]>('/groups'),
  create: (body: { slug: string; name: string; schema_name?: string }) =>
    rootApi.post<Group>('/groups', body),
  rename: (slug: string, name: string) =>
    rootApi.patch<Group>(`/groups/${slug}`, { name }),
}
