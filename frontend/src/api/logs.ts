import { groupClient } from './http'
import type { PaginatedJobLogs } from './types'

export function logApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: async (params: { job_type?: string; status?: string; limit?: number; offset?: number }): Promise<PaginatedJobLogs> => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.job_type) q.set('job_type', params.job_type)
      if (params.status) q.set('status', params.status)
      if (params.limit != null) q.set('limit', String(params.limit))
      if (params.offset != null) q.set('offset', String(params.offset))
      const raw = await c.get<any>(`/logs?${q}`)
      // 구버전/미반영 백엔드가 평면 배열을 반환해도 앱이 죽지 않도록 정규화한다.
      if (Array.isArray(raw)) {
        const items = raw as PaginatedJobLogs['items']
        return { total: items.length, page: 1, page_size: params.limit ?? items.length, items }
      }
      return raw as PaginatedJobLogs
    },
  }
}
