import type { Video, VideoDetail } from './types'

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
    notify_source: raw.notify_source ?? null,
    summary,
    source_channel_name: raw.source_channel_name ?? null,
  }
}

/** ytdb VideoDetail(중첩 analysis) → 페이지용 평탄화 VideoDetail. */
export function toVideoDetail(raw: Record<string, any>): VideoDetail {
  const a = raw.analysis ?? null
  return {
    video_pk: raw.video_pk,
    video_id: raw.video_id,
    video_url: raw.video_url,
    title: raw.title,
    description: raw.description ?? null,
    thumbnail_url: raw.thumbnail_url ?? null,
    published_at: raw.published_at,
    duration_seconds: raw.duration_seconds ?? null,
    view_count: raw.view_count ?? null,
    like_count: raw.like_count ?? null,
    analysis_status: raw.analysis_status,
    analysis_error: raw.analysis_error ?? null,
    notified_at: raw.notified_at ?? null,
    notify_source: raw.notify_source ?? null,
    source_channel_name: raw.source_channel_name ?? null,
    channel_name: raw.channel_name ?? null,
    retry_count: raw.retry_count ?? null,
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    one_line: a?.one_line ?? null,
    headline: a?.headline ?? null,
    short_summary_md: a?.short_summary_md ?? null,
    full_analysis_md: a?.full_analysis_md ?? null,
    bullet_points: a?.bullet_points ?? null,
    key_points: a?.key_points ?? null,
    analysis_sections: a?.analysis_sections ?? null,
    insights: a?.insights ?? null,
    entities: a?.entities ?? null,
    sentiment: a?.sentiment ?? null,
    confidence_score: a?.confidence_score ?? null,
    model_name: a?.model_name ?? null,
    analyzed_at: a?.analyzed_at ?? null,
  }
}
