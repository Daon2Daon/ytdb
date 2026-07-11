import { describe, it, expect } from 'vitest'
import { SETTING_DEFS, visibleCategories, visibleFields } from './defs'

describe('visibleCategories', () => {
  it('admin: 전체 카테고리 노출', () => {
    const keys = visibleCategories('admin').map((c) => c.key)
    expect(keys).toContain('database')
    expect(keys).toContain('ai_gateway')
  })
  it('user: database·ai_gateway 은닉', () => {
    const keys = visibleCategories('user').map((c) => c.key)
    expect(keys).not.toContain('database')
    expect(keys).not.toContain('ai_gateway')
    expect(keys).toContain('polling')
    expect(keys).toContain('prompts')
  })
  it('undefined role: user와 동일(안전 기본값)', () => {
    expect(visibleCategories(undefined).map((c) => c.key)).toEqual(
      visibleCategories('user').map((c) => c.key),
    )
  })
})

describe('visibleFields', () => {
  it('admin: 전체 필드 그대로', () => {
    expect(visibleFields('admin', 'polling', SETTING_DEFS.polling)).toEqual(SETTING_DEFS.polling)
  })
  it('user polling: youtube_api_key 제거', () => {
    const keys = visibleFields('user', 'polling', SETTING_DEFS.polling).map((d) => d.key)
    expect(keys).not.toContain('youtube_api_key')
    expect(keys).toContain('window_hours')
  })
  it('user notification: bot_token·chat_ids 제거', () => {
    const keys = visibleFields('user', 'notification', SETTING_DEFS.notification).map((d) => d.key)
    expect(keys).not.toContain('bot_token')
    expect(keys).not.toContain('chat_ids')
    expect(keys).toContain('enabled')
  })
  it('user 블록리스트 없는 카테고리: 그대로', () => {
    expect(visibleFields('user', 'digest', SETTING_DEFS.digest)).toEqual(SETTING_DEFS.digest)
  })
})
