// 댓글 분석 화면의 순수 계산. 백엔드 quota_service.credits_for와 같은 규칙을 따른다.

export const CREDIT_UNIT_COMMENTS = 1000
export const LIMIT_OPTIONS = [500, 1000, 2000, 5000]

export type CreditState = { unlimited: boolean; perAnalysisMax: number }

/** 가중 크레딧: 1회 = 댓글 1,000건. 하한 1회. */
export function creditCost(limit: number): number {
  return Math.max(1, Math.ceil(Math.max(0, limit) / CREDIT_UNIT_COMMENTS))
}

/** 드롭다운 항목 활성 여부. 관리자(unlimited)는 전부 열린다. */
export function optionEnabled(limit: number, state: CreditState): boolean {
  return state.unlimited || limit <= state.perAnalysisMax
}

/**
 * 신선도 판정. 현재 댓글 수(videos.comment_count)나 분석 시점 총수가 없으면
 * 판단하지 않는다 — 기존 영상은 comment_count가 아직 NULL일 수 있다.
 */
export function freshness(
  currentCount: number | null | undefined,
  analyzedTotal: number | null | undefined,
): { stale: boolean; added: number } {
  if (currentCount == null || analyzedTotal == null) return { stale: false, added: 0 }
  const added = currentCount - analyzedTotal
  return added > 0 ? { stale: true, added } : { stale: false, added: 0 }
}

export type CardState = 'none' | 'running' | 'done' | 'stale' | 'failed'

/**
 * 카드가 보여줄 상태 하나로 접는다. 이 저장소의 프론트 테스트 컨벤션대로
 * 렌더링 대신 이 순수 함수로 분기를 검증한다(@testing-library 미설치).
 */
export function cardState(
  status: string | null | undefined,
  currentCount: number | null | undefined,
  analyzedTotal: number | null | undefined,
): CardState {
  if (!status) return 'none'
  if (status === 'pending' || status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  if (status === 'done') {
    return freshness(currentCount, analyzedTotal).stale ? 'stale' : 'done'
  }
  return 'none'
}

/**
 * 결과 블록을 그릴지 판정한다.
 *
 * 재분석은 같은 행을 덮어쓰므로(UNIQUE(video_pk)) 실패하면 status만 failed가 되고
 * result JSONB는 남는다. 그때도 직전 결과를 계속 보여줘 멀쩡한 분석이 화면에서
 * 사라지지 않게 한다. 다만 running 중에는 갱신 중임이 분명하도록 감춘다.
 */
export function showsResult(
  status: string | null | undefined,
  hasResult: boolean,
): boolean {
  if (!hasResult) return false
  return status === 'done' || status === 'failed'
}

/** 비율 막대용 백분율. 합이 0이면 전부 0. */
export function ratioPercents(
  positive: number | null,
  negative: number | null,
  neutral: number | null,
): { positive: number; negative: number; neutral: number } {
  const p = positive ?? 0
  const n = negative ?? 0
  const u = neutral ?? 0
  const total = p + n + u
  if (total === 0) return { positive: 0, negative: 0, neutral: 0 }
  return {
    positive: Math.round((p / total) * 100),
    negative: Math.round((n / total) * 100),
    neutral: Math.round((u / total) * 100),
  }
}
