import { describe, it, expect, vi, afterEach } from 'vitest'
import { groupClient } from './http'

afterEach(() => vi.restoreAllMocks())

function mockFetch(status: number, body: unknown) {
  const nullBody = status === 204 || status === 205 || status === 304
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(nullBody ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

describe('groupClient', () => {
  it('그룹 slug를 base 경로에 주입한다', async () => {
    const f = mockFetch(200, { ok: true })
    const client = groupClient('invest')
    await client.get('/videos?paged=1')
    expect(f).toHaveBeenCalledWith(
      '/api/groups/invest/videos?paged=1',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('비정상 응답이면 detail 메시지로 throw한다', async () => {
    mockFetch(400, { detail: 'DB 설정이 없습니다.' })
    const client = groupClient('invest')
    await expect(client.get('/stats')).rejects.toThrow('DB 설정이 없습니다.')
  })

  it('204는 undefined를 반환한다', async () => {
    mockFetch(204, {})
    const client = groupClient('invest')
    await expect(client.del('/videos/1')).resolves.toBeUndefined()
  })
})
