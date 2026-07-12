import { describe, expect, it } from 'vitest'
import { onboardingComplete, onboardingSteps } from './OnboardingChecklist.logic'

describe('onboardingSteps', () => {
  it('신규 사용자: 전부 미완', () => {
    const s = { groupCount: 0, channelCount: 0, destinationCount: 0 }
    expect(onboardingSteps(s).map((x) => x.done)).toEqual([false, false, false])
    expect(onboardingComplete(s)).toBe(false)
  })
  it('그룹+채널만: 텔레그램 미완', () => {
    const s = { groupCount: 1, channelCount: 2, destinationCount: 0 }
    expect(onboardingSteps(s).map((x) => x.done)).toEqual([true, true, false])
  })
  it('전부 완료 시 complete', () => {
    expect(onboardingComplete({ groupCount: 1, channelCount: 1, destinationCount: 1 })).toBe(true)
  })
})
