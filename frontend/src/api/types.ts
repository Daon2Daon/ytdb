export interface Group {
  group_id: number
  slug: string
  name: string
  schema_name: string
  is_active: boolean
  description: string | null
}

export interface VideoSummary {
  one_line: string
  headline: string | null
}

export interface Video {
  video_pk: number
  video_id: string
  video_url: string
  title: string
  thumbnail_url: string | null
  published_at: string
  duration_seconds: number | null
  view_count: number | null
  analysis_status: 'pending' | 'processing' | 'done' | 'failed'
  notified_at: string | null
  notify_source: 'telegram' | 'web' | null
  summary: VideoSummary | null
  source_channel_name: string | null
}

export interface PaginatedVideos {
  total: number
  page: number
  page_size: number
  items: Video[]
}

export interface Stats {
  total_channels: number
  active_channels: number
  total_videos: number
  analyzed_videos: number
  pending_videos: number
  failed_videos: number
  notified_videos: number
  total_tags: number
}

export interface DBHealthResponse {
  healthy: boolean
  message: string
  latency_ms: number | null
}

export interface GatewayHealthResponse {
  success: boolean
  message: string
  latency_ms?: number
}

export interface Channel {
  channel_pk: number
  channel_id: string
  channel_name: string
  channel_handle: string | null
  thumbnail_url: string | null
  category: string | null
  poll_interval_min: number
  is_active: boolean
  notify_enabled: boolean
  last_checked_at: string | null
  last_video_id: string | null
  created_at: string
}

export interface KeyPoint {
  timestamp?: string
  point?: string
}

export interface AnalysisSection {
  key: string
  title: string
  bullets: string[]
}

export interface VideoDetail {
  video_pk: number
  video_id: string
  video_url: string
  title: string
  description: string | null
  thumbnail_url: string | null
  published_at: string
  duration_seconds: number | null
  view_count: number | null
  like_count: number | null
  analysis_status: 'pending' | 'processing' | 'done' | 'failed'
  analysis_error: string | null
  notified_at: string | null
  notify_source: 'telegram' | 'web' | null
  source_channel_name: string | null
  channel_name: string | null
  retry_count: number | null
  tags: string[]
  one_line: string | null
  headline: string | null
  short_summary_md: string | null
  full_analysis_md: string | null
  bullet_points: string[] | null
  key_points: KeyPoint[] | null
  analysis_sections: AnalysisSection[] | null
  insights: string[] | null
  entities: unknown[] | null
  sentiment: string | null
  confidence_score: number | null
  model_name: string | null
  analyzed_at: string | null
}

export interface Tag {
  tag_pk: number
  name: string
  tag_type: string
  video_count: number
}

export interface JobLog {
  log_pk: number
  job_type: string
  channel_pk: number | null
  video_pk: number | null
  status: string
  message: string | null
  duration_ms: number | null
  started_at: string
}

export interface PaginatedJobLogs {
  total: number
  page: number
  page_size: number
  items: JobLog[]
}

export interface InstantAnalyzeResponse {
  video_pk: number
  video_id: string
  existing: boolean
  queued: boolean
}

export interface PollResponse {
  status: string
  channel_pk: number
  message: string
}

export interface TagCount { name: string; count: number }

export interface DigestScheduleConfig {
  id: string
  name: string
  enabled: boolean
  period_days: 1 | 7 | 30
  schedule_time: string
  schedule_day: string
  schedule_dom: number
  timezone: string
  category: string
  digest_prompt: string
  telegram_enabled: boolean
  sections?: DigestSection[]
}

export interface DigestSection {
  key: string
  kind: 'llm' | 'computed'
  title: string
  guide?: string
  body_md?: string
  data?: Record<string, unknown>
}

export interface Digest {
  digest_pk: number
  period_type: string
  period_weeks: number
  period_days: number | null
  digest_config_id: string | null
  config_name: string | null
  period_start: string
  period_end: string
  category: string | null
  video_count: number
  headline: string | null
  summary_md: string | null
  telegram_summary: string | null
  sentiment_breakdown: Record<string, number> | null
  top_tags: TagCount[] | null
  top_channels: TagCount[] | null
  digest_sections?: DigestSection[] | null
  status: string
  error: string | null
  created_at: string
}

export interface VideoNotifyResponse {
  success: boolean
  message: string
  notified_at: string | null
  notify_source: 'telegram' | 'web' | null
}
