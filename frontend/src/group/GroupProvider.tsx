import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate, Outlet } from 'react-router-dom'
import { groupApi } from '../api/groups'
import type { Group } from '../api/types'
import { GroupContext } from './useGroup'
import Spinner from '../components/Spinner'

export default function GroupProvider() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  // 목록(본인 소유) 밖의 그룹을 열람 중일 때(관리자 지원용) 개별 조회로 보정한 그룹.
  const [visiting, setVisiting] = useState<Group | null>(null)

  const reloadGroups = useCallback(async () => {
    const list = await groupApi.list()
    setGroups(list)
    return
  }, [])

  useEffect(() => {
    reloadGroups().finally(() => setLoading(false))
  }, [reloadGroups])

  // slug가 본인 목록에 없으면 개별 조회(관리자는 타인 그룹 접근 가능) 후,
  // 그래도 없으면 첫 그룹 또는 온보딩 랜딩으로 보정.
  useEffect(() => {
    if (loading) return
    if (slug && groups.some((g) => g.slug === slug)) {
      setVisiting(null)
      return
    }
    if (!slug) {
      navigate(groups.length > 0 ? `/g/${groups[0].slug}/` : '/', { replace: true })
      return
    }
    let cancelled = false
    groupApi
      .get(slug)
      .then((g) => {
        if (!cancelled) setVisiting(g)
      })
      .catch(() => {
        if (cancelled) return
        navigate(groups.length > 0 ? `/g/${groups[0].slug}/` : '/', { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [loading, groups, slug, navigate])

  if (loading) return <Spinner />

  const owned = groups.find((g) => g.slug === slug)
  const activeGroup = owned ?? (visiting?.slug === slug ? visiting : undefined)
  // 개별 조회 진행 중이거나 곧 리다이렉트됨.
  if (!activeGroup) return <Spinner />
  // 열람 중인 타 소유 그룹도 전환기에 보이도록 목록에 덧붙인다.
  const visibleGroups = owned ? groups : [...groups, activeGroup]

  return (
    <GroupContext.Provider
      value={{ groups: visibleGroups, activeSlug: slug ?? '', activeGroup, reloadGroups }}
    >
      <Outlet />
    </GroupContext.Provider>
  )
}
