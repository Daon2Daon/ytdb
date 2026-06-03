import { describe, it, expect } from 'vitest'
import { initialValue, toSaveItem } from './convert'
import type { FieldDef as _FieldDef } from './defs'
import type { SettingItem } from '../api/settings'

const item = (value: string | null): SettingItem => ({ key: 'k', value, value_type: 'string', is_secret: false })

describe('initialValue', () => {
  it('int_days: 저장 시간값을 일수로 환산', () => {
    expect(initialValue({ key: 'window_hours', label: '', type: 'int_days' }, item('168'))).toBe('7')
  })
  it('int_hours: 저장 분값을 시간으로 환산', () => {
    expect(initialValue({ key: 'iv', label: '', type: 'int_hours' }, item('720'))).toBe('12')
  })
  it('bool: 문자열 true → boolean', () => {
    expect(initialValue({ key: 'enabled', label: '', type: 'bool' }, item('true'))).toBe(true)
    expect(initialValue({ key: 'enabled', label: '', type: 'bool' }, item('false'))).toBe(false)
  })
  it('secret: 마스킹값을 프리필하지 않고 빈 문자열', () => {
    expect(initialValue({ key: 'api_key', label: '', secret: true }, item('****1234'))).toBe('')
  })
  it('chatlist: JSON 배열 파싱', () => {
    expect(initialValue({ key: 'chat_ids', label: '', type: 'chatlist' }, item('["-100","42"]'))).toEqual(['-100', '42'])
  })
})

describe('toSaveItem', () => {
  it('int_days: 일수 → 시간 저장(int)', () => {
    expect(toSaveItem({ key: 'window_hours', label: '', type: 'int_days' }, '7')).toEqual({ key: 'window_hours', value: '168', value_type: 'int', is_secret: false })
  })
  it('int_hours: 시간 → 분 저장(int)', () => {
    expect(toSaveItem({ key: 'iv', label: '', type: 'int_hours' }, '12')).toEqual({ key: 'iv', value: '720', value_type: 'int', is_secret: false })
  })
  it('bool → "true"/"false"', () => {
    expect(toSaveItem({ key: 'enabled', label: '', type: 'bool' }, true)).toEqual({ key: 'enabled', value: 'true', value_type: 'bool', is_secret: false })
  })
  it('secret → is_secret true, value_type string', () => {
    expect(toSaveItem({ key: 'api_key', label: '', secret: true }, 'sk-x')).toEqual({ key: 'api_key', value: 'sk-x', value_type: 'string', is_secret: true })
  })
  it('chatlist → JSON 문자열(json), 공백 제거', () => {
    expect(toSaveItem({ key: 'chat_ids', label: '', type: 'chatlist' }, [' -100 ', '', '42'])).toEqual({ key: 'chat_ids', value: '["-100","42"]', value_type: 'json', is_secret: false })
  })
  it('int/float/string value_type 매핑', () => {
    expect(toSaveItem({ key: 'p', label: '', type: 'int' }, '5')).toEqual({ key: 'p', value: '5', value_type: 'int', is_secret: false })
    expect(toSaveItem({ key: 'f', label: '', type: 'float' }, '0.3')).toEqual({ key: 'f', value: '0.3', value_type: 'float', is_secret: false })
    expect(toSaveItem({ key: 's', label: '' }, 'hi')).toEqual({ key: 's', value: 'hi', value_type: 'string', is_secret: false })
  })
})
