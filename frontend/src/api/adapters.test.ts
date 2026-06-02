import { describe, it, expect } from 'vitest'
import { toVideo, toVideoDetail } from './adapters'

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

describe('toVideoDetail', () => {
  it('중첩 analysis를 최상위로 평탄화한다', () => {
    const raw = {
      video_pk: 5, video_id: 'v5', video_url: 'u', title: 'T', description: '설명',
      thumbnail_url: null, published_at: '2026-06-01T00:00:00Z', duration_seconds: 300,
      view_count: 1000, like_count: 50, analysis_status: 'done', analysis_error: null,
      notified_at: null, tags: ['반도체', 'AI'],
      analysis: {
        one_line: '한 줄', headline: '헤드라인', short_summary_md: '요약',
        bullet_points: ['p1', 'p2'], full_analysis_md: '## 분석',
        key_points: [{ timestamp: '0:10', point: 'x' }], insights: ['i1'], entities: [],
        sentiment: '긍정', confidence_score: 0.8, model_name: 'gemini',
        analyzed_at: '2026-06-01T01:00:00Z',
      },
    }
    const v = toVideoDetail(raw)
    expect(v.full_analysis_md).toBe('## 분석')
    expect(v.headline).toBe('헤드라인')
    expect(v.bullet_points).toEqual(['p1', 'p2'])
    expect(v.tags).toEqual(['반도체', 'AI'])
    expect(v.confidence_score).toBe(0.8)
    expect(v.retry_count).toBeNull()
  })

  it('analysis가 null이면 분석 필드는 모두 null', () => {
    const raw = {
      video_pk: 6, video_id: 'v6', video_url: 'u', title: 'T', description: null,
      thumbnail_url: null, published_at: '2026-06-01T00:00:00Z', duration_seconds: null,
      view_count: null, like_count: null, analysis_status: 'pending', analysis_error: null,
      notified_at: null, tags: [], analysis: null,
    }
    const v = toVideoDetail(raw)
    expect(v.full_analysis_md).toBeNull()
    expect(v.headline).toBeNull()
    expect(v.bullet_points).toBeNull()
    expect(v.sentiment).toBeNull()
  })
})
