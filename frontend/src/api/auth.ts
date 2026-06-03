import { rootApi } from './http'

export interface MeResponse {
  auth_enabled: boolean
  authenticated: boolean
  username: string | null
}

export const authApi = {
  me: () => rootApi.get<MeResponse>('/auth/me'),
  login: (username: string, password: string) =>
    rootApi.post<{ username: string }>('/auth/login', { username, password }),
  logout: () => rootApi.post<void>('/auth/logout'),
}
