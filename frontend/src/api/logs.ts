import { groupClient } from './http'
import type { PaginatedJobLogs } from './types'

export function logApi(slug: string) {
  const c = groupClient(slug)
  return {
    listPaged: (params: { job_type?: string; status?: string; limit?: number; offset?: number }) => {
      const q = new URLSearchParams({ paged: '1' })
      if (params.job_type) q.set('job_type', params.job_type)
      if (params.status) q.set('status', params.status)
      if (params.limit != null) q.set('limit', String(params.limit))
      if (params.offset != null) q.set('offset', String(params.offset))
      return c.get<PaginatedJobLogs>(`/logs?${q}`)
    },
  }
}
