import { rootApi } from './http'

export interface AdminUser {
  user_id: number
  email: string
  display_name: string | null
  role: string
  status: string
  plan_id: number
  last_login_at: string | null
  created_at: string
}

export interface PlanInfo {
  plan_id: number
  slug: string
  name: string
  max_groups: number
  max_channels_total: number
  max_analyses_per_day: number
  max_video_minutes: number
  min_poll_interval_min: number
  is_default: boolean
}

export interface Invite {
  invite_id: number
  token: string
  plan_id: number
  memo: string | null
  expires_at: string
  used_by: number | null
  used_at: string | null
  created_at: string
}

export interface InviteCreated extends Invite {
  signup_url: string
}

export const adminApi = {
  users: () => rootApi.get<AdminUser[]>('/admin/users'),
  plans: () => rootApi.get<PlanInfo[]>('/admin/plans'),
  invites: () => rootApi.get<Invite[]>('/admin/invitations'),
  createInvite: (planSlug: string | null, memo: string, expiresDays: number) =>
    rootApi.post<InviteCreated>('/admin/invitations', {
      plan_slug: planSlug, memo: memo || null, expires_days: expiresDays,
    }),
  revokeInvite: (inviteId: number) => rootApi.del<void>(`/admin/invitations/${inviteId}`),
}
