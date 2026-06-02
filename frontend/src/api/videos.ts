import { groupClient } from './http'
import { toVideo } from './adapters'
import type { PaginatedVideos } from './types'

export function videoApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: async (params: {
      status?: string
      tag?: string
      limit?: number
      offset?: number
    }): Promise<PaginatedVideos> => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.status) q.set('status', params.status)
      if (params.tag) q.set('tag', params.tag)
      if (params.limit != null) q.set('limit', String(params.limit))
      if (params.offset != null) q.set('offset', String(params.offset))
      const raw = await c.get<any>(`/videos?${q}`)
      return {
        total: raw.total,
        page: raw.page,
        page_size: raw.page_size,
        items: (raw.items as any[]).map(toVideo),
      }
    },
  }
}
