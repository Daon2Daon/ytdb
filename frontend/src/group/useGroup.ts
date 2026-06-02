import { createContext, useContext } from 'react'
import type { Group } from '../api/types'

export interface GroupContextValue {
  groups: Group[]
  activeSlug: string
  activeGroup: Group | undefined
  reloadGroups: () => Promise<void>
}

export const GroupContext = createContext<GroupContextValue | null>(null)

export function useGroup(): GroupContextValue {
  const ctx = useContext(GroupContext)
  if (!ctx) throw new Error('useGroup must be used within GroupProvider')
  return ctx
}
