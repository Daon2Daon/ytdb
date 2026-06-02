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
