import { groupClient } from './http'
import type { Channel, PollResponse } from './types'

export function channelApi(slug: string) {
  const c = groupClient(slug)
  return {
    list: () => c.get<Channel[]>('/channels'),
    add: (body: {
      channel_input: string
      category?: string
      poll_interval_min?: number
      backfill?: boolean
    }) => c.post<Channel>('/channels', body),
    update: (
      pk: number,
      patch: Partial<Pick<Channel, 'is_active' | 'notify_enabled' | 'poll_interval_min' | 'category'>>,
    ) => c.patch<Channel>(`/channels/${pk}`, patch),
    remove: (pk: number) => c.del<void>(`/channels/${pk}`),
    poll: (pk: number) => c.post<PollResponse>(`/channels/${pk}/poll`),
  }
}
