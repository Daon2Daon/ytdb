import type { FieldDef } from './defs'
import type { SettingItem } from '../api/settings'

export type FormValue = string | boolean | string[] | object

/** 로드된 SettingItem → 폼 초기값(표시 단위로 환산). */
export function initialValue(def: FieldDef, item: SettingItem | undefined): FormValue {
  const raw = item?.value ?? null
  if (def.type === 'bool') return String(raw).toLowerCase() === 'true'
  if (def.type === 'chatlist') {
    try {
      const arr = JSON.parse(raw || '[]')
      return Array.isArray(arr) ? arr.map(String) : []
    } catch {
      return String(raw || '').split(',').map((s) => s.trim()).filter(Boolean)
    }
  }
  if (def.type === 'timelist') {
    try {
      const arr = JSON.parse(raw || '[]')
      return Array.isArray(arr) ? arr.map(String) : []
    } catch {
      return String(raw || '').split(',').map((s) => s.trim()).filter(Boolean)
    }
  }
  if (def.type === 'template_builder') {
    try {
      const parsed = JSON.parse(raw || '{}')
      return (parsed && Array.isArray(parsed.fields)) ? parsed : { fields: [] }
    } catch {
      return { fields: [] }
    }
  }
  if (def.secret) return ''
  if (def.type === 'int_days') {
    const hours = Number(raw || 0)
    return String(Number.isFinite(hours) ? Math.max(0, Math.floor(hours / 24)) : 0)
  }
  if (def.type === 'int_hours') {
    const mins = Number(raw || 0)
    return String(Number.isFinite(mins) ? Math.max(0, Math.floor(mins / 60)) : 0)
  }
  return raw == null ? '' : String(raw)
}

/** 폼값 → 저장용 SettingItem(역환산 + value_type). */
export function toSaveItem(def: FieldDef, value: FormValue): SettingItem {
  if (def.type === 'chatlist') {
    const ids = (value as string[]).map((s) => s.trim()).filter(Boolean)
    return { key: def.key, value: JSON.stringify(ids), value_type: 'json', is_secret: false }
  }
  if (def.type === 'timelist') {
    const times = (value as string[]).map((s) => s.trim()).filter(Boolean)
    return { key: def.key, value: JSON.stringify(times), value_type: 'json', is_secret: false }
  }
  if (def.type === 'template_builder') {
    return { key: def.key, value: JSON.stringify(value), value_type: 'json', is_secret: false }
  }
  if (def.type === 'bool') {
    return { key: def.key, value: value ? 'true' : 'false', value_type: 'bool', is_secret: false }
  }
  if (def.type === 'int_days') {
    const d = parseInt((value as string) || '0', 10)
    const safe = Number.isFinite(d) && d > 0 ? d : 0
    return { key: def.key, value: String(safe * 24), value_type: 'int', is_secret: false }
  }
  if (def.type === 'int_hours') {
    const h = parseInt((value as string) || '0', 10)
    const safe = Number.isFinite(h) && h > 0 ? h : 0
    return { key: def.key, value: String(safe * 60), value_type: 'int', is_secret: false }
  }
  if (def.secret) {
    return { key: def.key, value: value as string, value_type: 'string', is_secret: true }
  }
  if (def.type === 'dest_select') {
    // 미지정('')은 백엔드에서 clear로 처리(빈 문자열 그대로 전송).
    return { key: def.key, value: value as string, value_type: 'int', is_secret: false }
  }
  const value_type = def.type === 'int' ? 'int' : def.type === 'float' ? 'float' : 'string'
  return { key: def.key, value: value as string, value_type, is_secret: false }
}
