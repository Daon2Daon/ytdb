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

  const reloadGroups = useCallback(async () => {
    const list = await groupApi.list()
    setGroups(list)
    return
  }, [])

  useEffect(() => {
    reloadGroups().finally(() => setLoading(false))
  }, [reloadGroups])

  // slug가 비었거나 목록에 없으면 첫 그룹으로 보정.
  useEffect(() => {
    if (loading) return
    if (groups.length === 0) return
    const found = slug && groups.some((g) => g.slug === slug)
    if (!found) navigate(`/g/${groups[0].slug}/`, { replace: true })
  }, [loading, groups, slug, navigate])

  if (loading) return <Spinner />
  if (groups.length === 0) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        그룹이 없습니다. 그룹을 먼저 생성하세요. (v1b에서 생성 UI 제공)
      </div>
    )
  }

  const activeGroup = groups.find((g) => g.slug === slug)
  return (
    <GroupContext.Provider
      value={{ groups, activeSlug: slug ?? '', activeGroup, reloadGroups }}
    >
      <Outlet />
    </GroupContext.Provider>
  )
}
