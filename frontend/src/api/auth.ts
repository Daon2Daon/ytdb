import { rootApi } from './http'

export interface AuthUser {
  email: string
  display_name: string | null
  role: 'admin' | 'user'
}

export interface MeResponse {
  auth_enabled: boolean
  authenticated: boolean
  user: AuthUser | null
}

export const authApi = {
  me: () => rootApi.get<MeResponse>('/auth/me'),
  login: (email: string, password: string) =>
    rootApi.post<AuthUser>('/auth/login', { email, password }),
  signup: (token: string, email: string, password: string, displayName: string) =>
    rootApi.post<AuthUser>('/auth/signup', {
      token, email, password, display_name: displayName || null,
    }),
  logout: () => rootApi.post<void>('/auth/logout'),
}
