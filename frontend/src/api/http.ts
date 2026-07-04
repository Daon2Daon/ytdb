// 세션 만료 등으로 임의 API가 401을 반환할 때 호출되는 전역 핸들러(AuthProvider가 등록).
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  })
  // /auth/ 호출(로그인 시도 등)의 401은 전역 핸들러로 넘기지 않는다.
  if (resp.status === 401 && !url.includes('/auth/')) {
    onUnauthorized?.()
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => null)
    const detail = data && typeof data.detail === 'string' ? data.detail : resp.statusText
    throw new Error(detail)
  }
  if (resp.status === 204) return undefined as T
  return resp.json()
}

export function groupClient(slug: string) {
  const base = `/api/groups/${slug}`
  return {
    get: <T>(path: string) => request<T>(`${base}${path}`, { method: 'GET' }),
    post: <T>(path: string, body?: unknown) =>
      request<T>(`${base}${path}`, {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    patch: <T>(path: string, body: unknown) =>
      request<T>(`${base}${path}`, { method: 'PATCH', body: JSON.stringify(body) }),
    put: <T>(path: string, body: unknown) =>
      request<T>(`${base}${path}`, { method: 'PUT', body: JSON.stringify(body) }),
    del: <T>(path: string) => request<T>(`${base}${path}`, { method: 'DELETE' }),
  }
}

export type GroupClient = ReturnType<typeof groupClient>

// 전역(그룹 비종속) 호출용.
export const rootApi = {
  get: <T>(path: string) => request<T>(`/api${path}`, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(`/api${path}`, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(`/api${path}`, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(`/api${path}`, { method: 'DELETE' }),
}
