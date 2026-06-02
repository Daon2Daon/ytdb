import { groupClient } from './http'
import type { Tag } from './types'

export function tagApi(slug: string) {
  return {
    list: (minCount = 1, limit = 200) =>
      groupClient(slug).get<Tag[]>(`/tags?min_count=${minCount}&limit=${limit}`),
  }
}
