import { rootApi } from './http'

export interface MyUsageResponse {
  plan_name: string
  plan_slug: string
  plan_expires_at: string | null
  unlimited: boolean
  limits: {
    max_groups: number
    max_channels_total: number
    max_analyses_per_day: number
    max_video_minutes: number
    min_poll_interval_min: number
    monthly_cost_budget_usd: number | null
  } | null
  usage: {
    group_count: number
    channel_count: number
    today_analyses: number
    month_cost_usd: number
  }
}

export interface TelegramDestination {
  dest_id: number
  chat_kind: string
  title: string | null
  is_active: boolean
  linked_at: string
}

export interface TelegramLinkResponse {
  deep_link: string
  expires_in_sec: number
}

export const meApi = {
  usage: () => rootApi.get<MyUsageResponse>('/me/usage'),
  telegramLinkToken: () => rootApi.post<TelegramLinkResponse>('/me/telegram/link-token', {}),
  telegramDestinations: () => rootApi.get<TelegramDestination[]>('/me/telegram/destinations'),
  deleteTelegramDestination: (destId: number) =>
    rootApi.del<void>(`/me/telegram/destinations/${destId}`),
}
