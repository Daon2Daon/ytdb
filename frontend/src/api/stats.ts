import { groupClient } from './http'
import type { Stats } from './types'

export function statsApi(slug: string) {
  return {
    get: () => groupClient(slug).get<Stats>('/stats'),
  }
}
