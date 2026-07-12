import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { useGroup } from '../group/useGroup'
import { channelApi } from '../api/channels'
import { meApi } from '../api/me'
import {
  onboardingComplete,
  onboardingSteps,
  type OnboardingState,
} from './OnboardingChecklist.logic'

/**
 * 온보딩 상태 조합 훅 (GroupProvider 컨텍스트 내부에서만 사용).
 * 로딩 중이면 null.
 */
export function useOnboardingState(): OnboardingState | null {
  const { groups, activeSlug } = useGroup()
  const [channelCount, setChannelCount] = useState<number | null>(null)
  const [destinationCount, setDestinationCount] = useState<number | null>(null)

  useEffect(() => {
    let alive = true
    if (!activeSlug) {
      setChannelCount(0)
      return
    }
    channelApi(activeSlug)
      .list()
      .then((cs) => alive && setChannelCount(cs.length))
      .catch(() => alive && setChannelCount(0))
    return () => {
      alive = false
    }
  }, [activeSlug])

  useEffect(() => {
    let alive = true
    meApi
      .telegramDestinations()
      .then((ds) => alive && setDestinationCount(ds.length))
      .catch(() => alive && setDestinationCount(0))
    return () => {
      alive = false
    }
  }, [])

  if (channelCount === null || destinationCount === null) return null
  return { groupCount: groups.length, channelCount, destinationCount }
}

/**
 * 순수 표현 카드. 완료 시 null. 각 미완 스텝은 실제 경로로 연결.
 * activeSlug 없으면 채널 링크는 텍스트로만 표시(그룹 0개 랜딩).
 */
export function OnboardingCard({
  state,
  activeSlug,
}: {
  state: OnboardingState
  activeSlug?: string
}) {
  if (onboardingComplete(state)) return null
  const steps = onboardingSteps(state)

  const linkFor = (key: string): string | null => {
    if (key === 'channel') return activeSlug ? `/g/${activeSlug}/channels` : null
    if (key === 'telegram') return '/me'
    return null
  }

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
      <h2 className="text-lg font-semibold text-gray-800">시작하기</h2>
      <ul className="space-y-2">
        {steps.map((step, idx) => {
          const to = step.done ? null : linkFor(step.key)
          const marker = step.done ? (
            <span className="text-green-600 font-bold">✓</span>
          ) : (
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-gray-300 text-xs text-gray-500">
              {idx + 1}
            </span>
          )
          const label = step.done ? (
            <span className="text-sm text-gray-400 line-through">{step.label}</span>
          ) : to ? (
            <Link to={to} className="text-sm text-blue-600 hover:underline">
              {step.label}
            </Link>
          ) : (
            <span className="text-sm text-gray-700">{step.label}</span>
          )
          return (
            <li key={step.key} className="flex items-center gap-2">
              {marker}
              {label}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/**
 * 대시보드용 자동 카드. 사용자(role='user')가 아니면 숨김.
 * 로딩 중이거나 모든 스텝 완료 시 null.
 */
export default function OnboardingChecklist() {
  const { user } = useAuth()
  const { activeSlug } = useGroup()
  const state = useOnboardingState()
  if (user?.role !== 'user') return null
  if (!state) return null
  return <OnboardingCard state={state} activeSlug={activeSlug} />
}
