export interface OnboardingState {
  groupCount: number
  channelCount: number // 현재 활성 그룹 기준 (그룹 없으면 0)
  destinationCount: number
}

export interface OnboardingStep {
  key: string
  label: string
  done: boolean
}

export function onboardingSteps(s: OnboardingState): OnboardingStep[] {
  return [
    { key: 'group', label: '모니터링 그룹 만들기', done: s.groupCount > 0 },
    { key: 'channel', label: '채널 추가하기', done: s.channelCount > 0 },
    { key: 'telegram', label: '텔레그램 연결하기', done: s.destinationCount > 0 },
  ]
}

export function onboardingComplete(s: OnboardingState): boolean {
  return onboardingSteps(s).every((x) => x.done)
}
