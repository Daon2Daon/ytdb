import { describe, it, expect } from 'vitest'
import {
  cardState,
  creditCost,
  freshness,
  optionEnabled,
  ratioPercents,
} from './CommentAnalysis.logic'

describe('creditCost', () => {
  it('1,000건 단위로 올림하되 하한은 1회', () => {
    expect(creditCost(500)).toBe(1)
    expect(creditCost(1000)).toBe(1)
    expect(creditCost(1001)).toBe(2)
    expect(creditCost(2000)).toBe(2)
    expect(creditCost(5000)).toBe(5)
  })
})

describe('optionEnabled', () => {
  it('관리자는 모든 수량이 열린다', () => {
    expect(optionEnabled(5000, { unlimited: true, perAnalysisMax: 1000 })).toBe(true)
  })
  it('플랜 상한을 넘는 수량은 비활성', () => {
    expect(optionEnabled(2000, { unlimited: false, perAnalysisMax: 1000 })).toBe(false)
    expect(optionEnabled(1000, { unlimited: false, perAnalysisMax: 1000 })).toBe(true)
  })
})

describe('freshness', () => {
  it('현재 댓글 수를 모르면 배너를 띄우지 않는다', () => {
    expect(freshness(null, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('분석 시점 총수를 모르면 배너를 띄우지 않는다', () => {
    expect(freshness(1200, null)).toEqual({ stale: false, added: 0 })
  })
  it('변화가 없으면 신선하다', () => {
    expect(freshness(1000, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('줄어들었으면 배너를 띄우지 않는다', () => {
    expect(freshness(900, 1000)).toEqual({ stale: false, added: 0 })
  })
  it('늘어난 만큼 added를 계산한다', () => {
    expect(freshness(1087, 1000)).toEqual({ stale: true, added: 87 })
  })
})

describe('ratioPercents', () => {
  it('합이 0이면 모두 0', () => {
    expect(ratioPercents(0, 0, 0)).toEqual({ positive: 0, negative: 0, neutral: 0 })
  })
  it('백분율을 반올림한다', () => {
    expect(ratioPercents(50, 30, 20)).toEqual({ positive: 50, negative: 30, neutral: 20 })
  })
  it('null은 0으로 취급한다', () => {
    expect(ratioPercents(null, null, null)).toEqual({ positive: 0, negative: 0, neutral: 0 })
  })
})

describe('cardState', () => {
  it('분석 기록이 없으면 none', () => {
    expect(cardState(null, null, null)).toBe('none')
    expect(cardState(undefined, 100, 100)).toBe('none')
  })
  it('pending과 running은 running', () => {
    expect(cardState('pending', null, null)).toBe('running')
    expect(cardState('running', null, null)).toBe('running')
  })
  it('failed는 failed', () => {
    expect(cardState('failed', null, null)).toBe('failed')
  })
  it('done이고 댓글이 안 늘었으면 done', () => {
    expect(cardState('done', 1000, 1000)).toBe('done')
  })
  it('done이고 댓글이 늘었으면 stale', () => {
    expect(cardState('done', 1087, 1000)).toBe('stale')
  })
  it('done이지만 현재 댓글 수를 모르면 done', () => {
    expect(cardState('done', null, 1000)).toBe('done')
  })
})
