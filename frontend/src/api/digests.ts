import { groupClient } from './http'
import type { Digest } from './types'

export function digestApi(slug: string) {
  const c = groupClient(slug)
  return {
    list: () => c.get<Digest[]>('/digests'),
    get: (pk: number) => c.get<Digest>(`/digests/${pk}`),
    remove: (pk: number) => c.del<void>(`/digests/${pk}`),
    generate: (digestConfigId?: string) =>
      c.post<Digest>('/digests/generate', {
        save: true,
        ...(digestConfigId ? { digest_config_id: digestConfigId } : {}),
      }),
  }
}
