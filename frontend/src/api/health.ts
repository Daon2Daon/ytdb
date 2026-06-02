import { groupClient } from './http'
import type { DBHealthResponse, GatewayHealthResponse } from './types'

export function healthApi(slug: string) {
  const c = groupClient(slug)
  return {
    db: () => c.get<DBHealthResponse>('/health/db'),
    gateway: () => c.post<GatewayHealthResponse>('/health/gateway'),
  }
}
