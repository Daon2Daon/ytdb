import type { Video } from './types'

/** ytdb VideoListItem(평면 headline/one_line) → 페이지용 Video(summary 중첩). */
export function toVideo(raw: Record<string, any>): Video {
  const oneLine: string | null = raw.one_line ?? null
  const headline: string | null = raw.headline ?? null
  const summary = oneLine ? { one_line: oneLine, headline } : null
  return {
    video_pk: raw.video_pk,
    video_id: raw.video_id,
    video_url: raw.video_url,
    title: raw.title,
    thumbnail_url: raw.thumbnail_url ?? null,
    published_at: raw.published_at,
    duration_seconds: raw.duration_seconds ?? null,
    view_count: raw.view_count ?? null,
    analysis_status: raw.analysis_status,
    notified_at: raw.notified_at ?? null,
    summary,
    source_channel_name: raw.source_channel_name ?? null,
  }
}
