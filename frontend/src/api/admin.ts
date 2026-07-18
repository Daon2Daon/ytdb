import { rootApi } from './http'

export interface AdminUser {
  user_id: number
  email: string
  display_name: string | null
  role: string
  status: string
  plan_id: number
  plan_expires_at: string | null
  last_login_at: string | null
  created_at: string
  usage: AdminUserUsage | null
}

export interface AdminUserUsage {
  group_count: number
  channel_count: number
  today_analyses: number
  has_override: boolean
}

export interface UserLimits {
  max_groups: number | null
  max_channels_total: number | null
  max_analyses_per_day: number | null
  max_video_minutes: number | null
  min_poll_interval_min: number | null
  note: string | null
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

export interface AdminUsageRow {
  user_id: number | null
  email: string | null
  model: string
  purpose: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number | null
  null_cost_calls: number
}

export interface YtQuotaEntry {
  key_fp: string
  units: number
  pct: number
  is_system_key: boolean
}

export interface YtQuotaStatus {
  usage_date: string
  daily_quota: number
  entries: YtQuotaEntry[]
}

export interface MigrationResultOut {
  group_id: number
  slug: string
  schema_name: string
  status: 'ok' | 'failed' | 'skipped'
  error: string | null
  duration_ms: number
}

export interface MigrateSchemasResponse {
  results: MigrationResultOut[]
  summary: { ok: number; failed: number; skipped: number }
}

export interface AdminUsageResponse {
  window: string
  start: string
  end: string
  rows: AdminUsageRow[]
  total_cost_usd: number
  null_cost_row_count: number
  youtube: YtQuotaStatus | null
}

export interface GlobalSettingItem {
  key: string
  value: string
  is_secret: boolean
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
  patchUser: (userId: number, body: { status?: string; plan_id?: number; plan_expires_at?: string | null }) =>
    rootApi.patch<AdminUser>(`/admin/users/${userId}`, body),
  putUserLimits: (userId: number, body: UserLimits) =>
    rootApi.put<UserLimits & { user_id: number }>(`/admin/users/${userId}/limits`, body),
  deleteUserLimits: (userId: number) =>
    rootApi.del<void>(`/admin/users/${userId}/limits`),
  issueTempPassword: (userId: number) =>
    rootApi.post<{ temp_password: string }>(`/admin/users/${userId}/temp-password`),
  patchPlan: (planId: number, body: Partial<Omit<PlanInfo, 'plan_id' | 'slug' | 'is_default'>>) =>
    rootApi.patch<PlanInfo>(`/admin/plans/${planId}`, body),
  usage: (window: string) =>
    rootApi.get<AdminUsageResponse>(`/admin/usage?window=${window}`),
  migrateSchemas: () => rootApi.post<MigrateSchemasResponse>('/admin/migrate-schemas', {}),
  globalSettings: () => rootApi.get<GlobalSettingItem[]>('/admin/global-settings'),
  putGlobalSettings: (items: GlobalSettingItem[]) =>
    rootApi.put<GlobalSettingItem[]>('/admin/global-settings', { items }),
}
