import { rootApi } from './http'

export interface MyUsageResponse {
  plan_name: string
  plan_slug: string
  unlimited: boolean
  limits: {
    max_groups: number
    max_channels_total: number
    max_analyses_per_day: number
    max_video_minutes: number
    min_poll_interval_min: number
  } | null
  usage: {
    group_count: number
    channel_count: number
    today_analyses: number
  }
}

export const meApi = {
  usage: () => rootApi.get<MyUsageResponse>('/me/usage'),
}
