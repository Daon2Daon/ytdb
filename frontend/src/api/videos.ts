import { groupClient } from './http'
import { toVideo, toVideoDetail } from './adapters'
import type { PaginatedVideos, VideoDetail, VideoNotifyResponse } from './types'

export function videoApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: async (params: {
      status?: string
      tag?: string
      channel_pk?: number
      limit?: number
      offset?: number
    }): Promise<PaginatedVideos> => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.status) q.set('status', params.status)
      if (params.tag) q.set('tag', params.tag)
      if (params.channel_pk != null) q.set('channel_pk', String(params.channel_pk))
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
    get: async (pk: number): Promise<VideoDetail> => toVideoDetail(await c.get<any>(`/videos/${pk}`)),
    remove: (pk: number) => c.del<void>(`/videos/${pk}`),
    analyzeNow: (pk: number, customPrompt?: string) =>
      c.post<{ status: string; video_pk: number }>(`/videos/${pk}/analyze-now`, { custom_prompt: customPrompt ?? null }),
    notify: (pk: number, force = false) =>
      c.post<VideoNotifyResponse>(`/videos/${pk}/notify`, { force }),
  }
}
