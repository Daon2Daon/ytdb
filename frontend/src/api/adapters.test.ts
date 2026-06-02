import { describe, it, expect } from 'vitest'
import { toVideo } from './adapters'

describe('toVideo', () => {
  it('평면 headline/one_line을 summary로 감싼다', () => {
    const raw = {
      video_pk: 1,
      video_id: 'abc',
      video_url: 'https://y/abc',
      title: 'T',
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: 120,
      analysis_status: 'done',
      notified_at: null,
      headline: '헤드라인',
      one_line: '한 줄',
    }
    const v = toVideo(raw)
    expect(v.summary).toEqual({ one_line: '한 줄', headline: '헤드라인' })
    expect(v.view_count).toBeNull()
    expect(v.source_channel_name).toBeNull()
  })

  it('one_line이 없으면 summary는 null', () => {
    const raw = {
      video_pk: 2,
      video_id: 'd',
      video_url: 'u',
      title: 'T2',
      thumbnail_url: null,
      published_at: '2026-06-01T00:00:00Z',
      duration_seconds: null,
      analysis_status: 'pending',
      notified_at: null,
      headline: null,
      one_line: null,
    }
    const v = toVideo(raw)
    expect(v.summary).toBeNull()
  })
})
