# React 마이그레이션 Plan 3 — 설정 6종 + 그룹 관리 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 그룹별 설정 6종(Database·AI Gateway·Monitoring·Notification·Prompts·Digest) 편집 화면과 그룹 생성/이름수정 UI를 React로 구현해, "그룹 생성 → 설정 → 모니터링" 전체 흐름을 새 UI 안에서 완결한다.

**Architecture:** ytdb 설정 API는 범용 key/value 모델(`GET/PUT /api/groups/{slug}/settings/{category}`, `SettingItem{key,value,value_type,is_secret}`)이다. 따라서 my-assistant의 타입별 설정 페이지를 복사하지 않고, **필드 정의 테이블(`SETTING_DEFS`)로 구동되는 범용 React 설정 폼**을 만든다(vanilla `app.js`의 검증된 방식 이식). 모든 설정 엔드포인트가 이미 존재하므로 **백엔드 변경은 없다**. 그룹 CRUD도 기존 `/api/groups` 엔드포인트를 사용한다.

**Tech Stack:** React 18 · TypeScript · React Router v6 · Tailwind · Vitest

**관련 스펙:** `docs/superpowers/specs/2026-06-03-react-migration-design.md`
**선행:** Plan 1·2 완료 — `frontend/` 앱, `groupClient`, `GroupProvider`/`useGroup`, `Layout`, 운영 페이지들이 존재. `groupApi`(`api/groups.ts`)에 `list/create/rename`가 이미 있다.

**참고 원본 (읽기 전용):** vanilla 설정 로직 `app/static/app.js` (SETTING_DEFS 431~537행, fieldHtml/saveSettings 581~739행), 그룹 모달 75~128행.

---

## 비목표
- 백엔드 변경 없음(설정/그룹 엔드포인트 전부 기존).
- 주간 리뷰 화면, 텔레그램 수동발송, 커스텀 프롬프트 → Plan 4.
- 그룹 삭제 UI는 범위 외(실수 위험 큼; 필요 시 별도). 생성/이름수정만.

---

## File Structure (프론트엔드 전용, `frontend/src/`)
- Create `api/settings.ts` — `settingsApi(slug)`: `get(category)`, `put(category, items)`, `gatewayModels()`; type `SettingItem`
- Create `settings/defs.ts` — `SETTING_DEFS`(카테고리별 필드 정의), `SETTING_CATEGORIES`(순서·라벨), 타입 `FieldDef`/`SettingCategory`
- Create `settings/convert.ts` — 순수 변환 헬퍼(`initialValue`, `toSaveItem`) + 단위 환산
- Create `settings/convert.test.ts` — convert TDD
- Create `components/SettingsForm.tsx` — 필드 정의 기반 폼(타입별 렌더 + 저장)
- Create `pages/Settings.tsx` — 라우트 `:category`로 정의/값 로드 → SettingsForm
- Create `components/GroupModals.tsx` — 그룹 생성/이름수정 모달
- Modify `components/Layout.tsx` — 설정 네비 섹션 + 헤더에 그룹 생성/수정 버튼
- Modify `App.tsx` — `settings/:category` 라우트 추가

---

## Task 1: 설정 API 모듈

**Files:** Create `frontend/src/api/settings.ts`

- [ ] **Step 1:** Create `frontend/src/api/settings.ts`:
```ts
import { groupClient } from './http'

export interface SettingItem {
  key: string
  value: string | null
  value_type: 'string' | 'int' | 'float' | 'bool' | 'json'
  is_secret: boolean
  description?: string | null
}

export function settingsApi(slug: string) {
  const c = groupClient(slug)
  return {
    get: (category: string) => c.get<SettingItem[]>(`/settings/${category}`),
    put: (category: string, items: SettingItem[]) =>
      c.put<SettingItem[]>(`/settings/${category}`, { items }),
    gatewayModels: () => c.get<string[]>(`/settings/ai_gateway/models`),
  }
}
```
- [ ] **Step 2:** `frontend/src/api/http.ts`의 `groupClient`에 `put`이 없으면 추가한다. 현재 groupClient는 `get/post/patch/del`만 있을 수 있다. 파일을 열어 확인하고, `patch` 정의 바로 아래에 `put`을 추가:
```ts
    put: <T>(path: string, body: unknown) =>
      request<T>(`${base}${path}`, { method: 'PUT', body: JSON.stringify(body) }),
```
(이미 있으면 생략.)
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` → 에러 없음(아직 미사용 모듈).
- [ ] **Step 4:** Commit
```bash
git add frontend/src/api/settings.ts frontend/src/api/http.ts
git commit -m "feat: settings API 모듈(get/put/gatewayModels) + groupClient.put"
```

---

## Task 2: 필드 정의 테이블

vanilla `app.js`의 `SETTING_DEFS`를 TS로 옮긴다. 각 카테고리의 필드와 타입을 정의.

**Files:** Create `frontend/src/settings/defs.ts`

- [ ] **Step 1:** Create `frontend/src/settings/defs.ts`:
```ts
export type FieldType =
  | 'string' | 'int' | 'float' | 'textarea' | 'bool'
  | 'select' | 'model_select' | 'chatlist' | 'int_days' | 'int_hours'

export interface FieldDef {
  key: string
  label: string
  type?: FieldType        // 미지정 시 'string'
  secret?: boolean        // password 입력(빈 값=기존 보존)
  options?: string[]      // select 전용
  help?: string
}

export interface SettingCategory {
  key: string
  label: string
}

// 좌측 설정 네비 순서/라벨 (route param = key)
export const SETTING_CATEGORIES: SettingCategory[] = [
  { key: 'database', label: 'Database' },
  { key: 'ai_gateway', label: 'AI Gateway' },
  { key: 'polling', label: 'Monitoring' },
  { key: 'notification', label: 'Notification' },
  { key: 'prompts', label: 'Prompts' },
  { key: 'digest', label: 'Digest' },
]

export const SETTING_DEFS: Record<string, FieldDef[]> = {
  ai_gateway: [
    { key: 'base_url', label: '게이트웨이 Base URL', help: '예: http://100.114.126.67:4000 또는 http://litellm:4000' },
    { key: 'api_key', label: 'API 키', secret: true, help: 'litellm 게이트웨이 인증 키' },
    { key: 'primary_model', label: '기본 모델 (경로 A)', type: 'model_select', help: '영상 분석 1차 호출 모델' },
    { key: 'fallback_model', label: '폴백 모델 (경로 B)', type: 'model_select', help: '기본 모델 실패 시 재시도 모델' },
    { key: 'temperature', label: 'temperature', type: 'float', help: '0~1 권장. 낮을수록 일관적' },
    { key: 'max_tokens', label: 'max_tokens', type: 'int', help: 'LLM 최대 출력 길이' },
  ],
  prompts: [
    { key: 'analysis_prompt', label: '분석 프롬프트', type: 'textarea' },
    { key: 'digest_prompt', label: '주간 리뷰 프롬프트', type: 'textarea' },
  ],
  database: [
    { key: 'host', label: '호스트' },
    { key: 'port', label: '포트', type: 'int' },
    { key: 'dbname', label: 'DB 이름' },
    { key: 'username', label: '사용자' },
    { key: 'password', label: '비밀번호', secret: true },
    { key: 'sslmode', label: 'sslmode', type: 'select', options: ['disable', 'prefer', 'require'], help: 'prefer 권장' },
  ],
  polling: [
    { key: 'youtube_api_key', label: 'YouTube API 키', secret: true },
    { key: 'window_hours', label: '최신 영상 수집 범위', type: 'int_days', help: '최근 N일 이내 업로드 영상만 수집' },
    { key: 'default_channel_interval_min', label: '새 영상 확인 주기', type: 'int_hours', help: '채널 기본 확인 주기(시간)' },
    { key: 'max_concurrent_channels', label: '동시 점검 채널 수', type: 'int' },
    { key: 'pending_analysis_interval_min', label: 'AI 분석 주기 (분)', type: 'int', help: '대기 영상 분석 스케줄 간격(분)' },
    { key: 'max_concurrent_analyses', label: 'AI 동시 요약 수', type: 'int' },
  ],
  notification: [
    { key: 'enabled', label: '알림 활성화', type: 'bool' },
    { key: 'bot_token', label: '텔레그램 봇 토큰', secret: true },
    { key: 'chat_ids', label: 'Chat ID 목록', type: 'chatlist' },
    { key: 'parse_mode', label: 'parse_mode', type: 'select', options: ['HTML', 'MarkdownV2', 'None'], help: '일반적으로 HTML 권장' },
  ],
  digest: [
    { key: 'enabled', label: '주간 리뷰 자동 생성', type: 'bool' },
    { key: 'period_weeks', label: '집계 기간(주)', type: 'int' },
    { key: 'schedule_day', label: '실행 요일', type: 'select', options: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] },
    { key: 'schedule_time', label: '실행 시각(HH:MM)' },
    { key: 'timezone', label: '시간대' },
    { key: 'telegram_enabled', label: '다이제스트 텔레그램 발송', type: 'bool' },
    { key: 'category', label: '카테고리 필터(선택)' },
  ],
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/settings/defs.ts
git commit -m "feat: 설정 필드 정의 테이블(SETTING_DEFS) 이식"
```

---

## Task 3: 변환 헬퍼 (TDD)

로드값→폼값(`initialValue`)과 폼값→저장아이템(`toSaveItem`) 순수 변환. 단위 환산(일↔시간, 시간↔분)과 시크릿 보존이 핵심.

**Files:** Create `frontend/src/settings/convert.ts`, `frontend/src/settings/convert.test.ts`

- [ ] **Step 1: 테스트 작성** — Create `frontend/src/settings/convert.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import { initialValue, toSaveItem } from './convert'
import type { FieldDef } from './defs'
import type { SettingItem } from '../api/settings'

const item = (value: string | null): SettingItem => ({ key: 'k', value, value_type: 'string', is_secret: false })

describe('initialValue', () => {
  it('int_days: 저장 시간값을 일수로 환산', () => {
    const def: FieldDef = { key: 'window_hours', label: '', type: 'int_days' }
    expect(initialValue(def, item('168'))).toBe('7')
  })
  it('int_hours: 저장 분값을 시간으로 환산', () => {
    const def: FieldDef = { key: 'iv', label: '', type: 'int_hours' }
    expect(initialValue(def, item('720'))).toBe('12')
  })
  it('bool: 문자열 true → boolean', () => {
    const def: FieldDef = { key: 'enabled', label: '', type: 'bool' }
    expect(initialValue(def, item('true'))).toBe(true)
    expect(initialValue(def, item('false'))).toBe(false)
  })
  it('secret: 마스킹값을 프리필하지 않고 빈 문자열', () => {
    const def: FieldDef = { key: 'api_key', label: '', secret: true }
    expect(initialValue(def, item('****1234'))).toBe('')
  })
  it('chatlist: JSON 배열 파싱', () => {
    const def: FieldDef = { key: 'chat_ids', label: '', type: 'chatlist' }
    expect(initialValue(def, item('["-100","42"]'))).toEqual(['-100', '42'])
  })
})

describe('toSaveItem', () => {
  it('int_days: 일수 → 시간 저장(int)', () => {
    const def: FieldDef = { key: 'window_hours', label: '', type: 'int_days' }
    expect(toSaveItem(def, '7')).toEqual({ key: 'window_hours', value: '168', value_type: 'int' })
  })
  it('int_hours: 시간 → 분 저장(int)', () => {
    const def: FieldDef = { key: 'iv', label: '', type: 'int_hours' }
    expect(toSaveItem(def, '12')).toEqual({ key: 'iv', value: '720', value_type: 'int' })
  })
  it('bool → "true"/"false"', () => {
    const def: FieldDef = { key: 'enabled', label: '', type: 'bool' }
    expect(toSaveItem(def, true)).toEqual({ key: 'enabled', value: 'true', value_type: 'bool' })
  })
  it('secret → is_secret true, value_type string', () => {
    const def: FieldDef = { key: 'api_key', label: '', secret: true }
    expect(toSaveItem(def, 'sk-x')).toEqual({ key: 'api_key', value: 'sk-x', value_type: 'string', is_secret: true })
  })
  it('chatlist → JSON 문자열(json), 공백 제거', () => {
    const def: FieldDef = { key: 'chat_ids', label: '', type: 'chatlist' }
    expect(toSaveItem(def, [' -100 ', '', '42'])).toEqual({ key: 'chat_ids', value: '["-100","42"]', value_type: 'json' })
  })
  it('int/float/string value_type 매핑', () => {
    expect(toSaveItem({ key: 'p', label: '', type: 'int' }, '5')).toEqual({ key: 'p', value: '5', value_type: 'int' })
    expect(toSaveItem({ key: 'f', label: '', type: 'float' }, '0.3')).toEqual({ key: 'f', value: '0.3', value_type: 'float' })
    expect(toSaveItem({ key: 's', label: '' }, 'hi')).toEqual({ key: 's', value: 'hi', value_type: 'string' })
  })
})
```
- [ ] **Step 2:** `cd frontend && npx vitest run src/settings/convert.test.ts` → FAIL(모듈 없음).
- [ ] **Step 3:** Create `frontend/src/settings/convert.ts`:
```ts
import type { FieldDef } from './defs'
import type { SettingItem } from '../api/settings'

export type FormValue = string | boolean | string[]

/** 로드된 SettingItem → 폼 초기값(표시 단위로 환산). */
export function initialValue(def: FieldDef, item: SettingItem | undefined): FormValue {
  const raw = item?.value ?? null
  if (def.type === 'bool') return String(raw).toLowerCase() === 'true' || raw === true
  if (def.type === 'chatlist') {
    try {
      const arr = JSON.parse(raw || '[]')
      return Array.isArray(arr) ? arr.map(String) : []
    } catch {
      return String(raw || '').split(',').map((s) => s.trim()).filter(Boolean)
    }
  }
  if (def.secret) return '' // 마스킹값을 프리필하지 않음(빈 값=기존 보존)
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
  const value_type = def.type === 'int' ? 'int' : def.type === 'float' ? 'float' : 'string'
  return { key: def.key, value: value as string, value_type, is_secret: false }
}
```
Note: the test's `expect(...).toEqual({key, value, value_type})` objects omit `is_secret`. To make `toEqual` pass, the test objects must match exactly. Since `toSaveItem` always sets `is_secret`, ADJUST the non-secret test expectations to include `is_secret: false` OR change the assertions. To keep it simple, UPDATE the test file's non-secret `toEqual(...)` expectations to include `is_secret: false` (and the secret one already implies it). Do this in Step 1 before running — i.e., append `, is_secret: false` to each non-secret expected object, and `is_secret: true` for the secret one. (Reconcile test and impl so both agree.)
- [ ] **Step 4:** `cd frontend && npx vitest run src/settings/convert.test.ts` → all passed.
- [ ] **Step 5:** Commit
```bash
git add frontend/src/settings/convert.ts frontend/src/settings/convert.test.ts
git commit -m "feat: 설정 값 변환 헬퍼(initialValue/toSaveItem) + TDD"
```

---

## Task 4: SettingsForm 컴포넌트

필드 정의 배열을 받아 타입별 입력을 렌더하고, 저장 시 `toSaveItem`으로 변환해 PUT한다.

**Files:** Create `frontend/src/components/SettingsForm.tsx`

- [ ] **Step 1:** Create `frontend/src/components/SettingsForm.tsx`:
```tsx
import { useMemo, useState } from 'react'
import type { FieldDef } from '../settings/defs'
import { initialValue, toSaveItem, type FormValue } from '../settings/convert'
import type { SettingItem } from '../api/settings'

interface Props {
  defs: FieldDef[]
  items: SettingItem[]          // 서버에서 로드된 현재 값(시크릿 마스킹)
  models?: string[]             // ai_gateway model_select용
  saving: boolean
  onSave: (items: SettingItem[]) => void
}

export default function SettingsForm({ defs, items, models = [], saving, onSave }: Props) {
  const itemMap = useMemo(() => {
    const m: Record<string, SettingItem> = {}
    items.forEach((i) => (m[i.key] = i))
    return m
  }, [items])

  const [form, setForm] = useState<Record<string, FormValue>>(() => {
    const init: Record<string, FormValue> = {}
    defs.forEach((d) => (init[d.key] = initialValue(d, itemMap[d.key])))
    return init
  })

  const set = (key: string, value: FormValue) => setForm((f) => ({ ...f, [key]: value }))

  const handleSave = () => onSave(defs.map((d) => toSaveItem(d, form[d.key])))

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-5 max-w-2xl">
      {defs.map((d) => (
        <Field
          key={d.key}
          def={d}
          value={form[d.key]}
          isSet={Boolean(itemMap[d.key]?.value)}
          models={models}
          onChange={(v) => set(d.key, v)}
        />
      ))}
      <div className="pt-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? '저장 중...' : '저장'}
        </button>
      </div>
    </div>
  )
}

function Field({
  def, value, isSet, models, onChange,
}: {
  def: FieldDef
  value: FormValue
  isSet: boolean
  models: string[]
  onChange: (v: FormValue) => void
}) {
  const help = def.help && <p className="text-xs text-gray-400 mt-1">{def.help}</p>

  if (def.type === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value as boolean} onChange={(e) => onChange(e.target.checked)} />
        <span className="font-medium text-gray-700">{def.label}</span>
        {help}
      </label>
    )
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{def.label}</label>
      {def.secret ? (
        <input
          type="password"
          value={value as string}
          placeholder={isSet ? '설정됨 (변경 시에만 입력)' : '미설정'}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      ) : def.type === 'textarea' ? (
        <textarea
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          rows={10}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
          spellCheck={false}
        />
      ) : def.type === 'select' ? (
        <select
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {(def.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : def.type === 'model_select' ? (
        <select
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">(선택 안 됨)</option>
          {value && !models.includes(value as string) && (
            <option value={value as string}>{value as string} (현재값)</option>
          )}
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      ) : def.type === 'chatlist' ? (
        <ChatList value={value as string[]} onChange={onChange} />
      ) : def.type === 'int_days' ? (
        <input type="number" min={0} value={value as string} onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      ) : def.type === 'int_hours' ? (
        <input type="number" min={0} value={value as string} onChange={(e) => onChange(e.target.value)}
          className="w-40 border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      ) : (
        <input
          type={def.type === 'int' || def.type === 'float' ? 'number' : 'text'}
          step={def.type === 'float' ? '0.1' : undefined}
          value={value as string}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      )}
      {def.type === 'int_days' && <span className="ml-2 text-xs text-gray-400">일</span>}
      {def.type === 'int_hours' && <span className="ml-2 text-xs text-gray-400">시간</span>}
      {help}
    </div>
  )
}

function ChatList({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const rows = value.length ? value : ['']
  const update = (i: number, v: string) => {
    const next = [...rows]
    next[i] = v
    onChange(next)
  }
  const add = () => onChange([...rows, ''])
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i))
  return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="flex gap-2">
          <input
            value={r}
            placeholder="-100... 또는 사용자 ID"
            onChange={(e) => update(i, e.target.value)}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm"
          />
          <button type="button" onClick={() => remove(i)} className="px-2 text-red-500 hover:bg-red-50 rounded">×</button>
        </div>
      ))}
      <button type="button" onClick={add} className="text-sm text-blue-600 hover:underline">+ Chat ID 추가</button>
    </div>
  )
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/components/SettingsForm.tsx
git commit -m "feat: SettingsForm(필드 정의 기반 범용 설정 폼)"
```

---

## Task 5: Settings 페이지

라우트 `:category`로 정의·값을 로드하고 SettingsForm을 렌더. ai_gateway면 모델 목록도 로드.

**Files:** Create `frontend/src/pages/Settings.tsx`

- [ ] **Step 1:** Create `frontend/src/pages/Settings.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { useParams, Navigate } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { settingsApi, type SettingItem } from '../api/settings'
import { SETTING_DEFS, SETTING_CATEGORIES } from '../settings/defs'
import SettingsForm from '../components/SettingsForm'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function Settings() {
  const { activeSlug } = useGroup()
  const { category } = useParams<{ category: string }>()
  const [items, setItems] = useState<SettingItem[]>([])
  const [models, setModels] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [modelMsg, setModelMsg] = useState<string | null>(null)

  const defs = category ? SETTING_DEFS[category] : undefined

  const load = async () => {
    if (!category || !defs) return
    setLoading(true)
    setError(null)
    try {
      setItems(await settingsApi(activeSlug).get(category))
      if (category === 'ai_gateway') {
        try {
          setModels(await settingsApi(activeSlug).gatewayModels())
          setModelMsg(null)
        } catch (e) {
          setModels([])
          setModelMsg((e as Error).message)
        }
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug, category])

  const handleSave = async (toSave: SettingItem[]) => {
    if (!category) return
    setSaving(true)
    try {
      const updated = await settingsApi(activeSlug).put(category, toSave)
      setItems(updated)
      setSavedAt(new Date().toLocaleTimeString('ko-KR'))
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!category || !defs) return <Navigate to={`/g/${activeSlug}/settings/database`} replace />
  if (loading) return <Spinner />

  const label = SETTING_CATEGORIES.find((c) => c.key === category)?.label ?? category

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">설정 · {label}</h1>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {category === 'ai_gateway' && modelMsg && (
        <div className="text-xs text-orange-600 bg-orange-50 border border-orange-200 rounded-lg px-3 py-2">
          모델 목록을 불러오지 못했습니다: {modelMsg} (base_url/api_key 저장 후 다시 시도)
        </div>
      )}
      <SettingsForm defs={defs} items={items} models={models} saving={saving} onSave={handleSave} />
    </div>
  )
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음(App.tsx가 아직 라우트 안 함).
- [ ] **Step 3:** Commit
```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat: Settings 페이지(카테고리별 로드/저장 + ai_gateway 모델)"
```

---

## Task 6: 그룹 생성/이름수정 모달

vanilla `app.js`의 newGroupModal/editGroupModal를 React로. `groupApi.create`/`groupApi.rename` + `useGroup().reloadGroups` 사용.

**Files:** Create `frontend/src/components/GroupModals.tsx`

- [ ] **Step 1:** Create `frontend/src/components/GroupModals.tsx`:
```tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { groupApi } from '../api/groups'
import { useGroup } from '../group/useGroup'

function ModalShell({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full space-y-4" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-bold text-gray-900">{title}</h3>
        {children}
      </div>
    </div>
  )
}

export function NewGroupModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { reloadGroups } = useGroup()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [schema, setSchema] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      await groupApi.create({ slug: slug.trim(), name: name.trim(), schema_name: schema.trim() || undefined })
      await reloadGroups()
      onClose()
      navigate(`/g/${slug.trim()}/`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="새 그룹" onClose={onClose}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (소문자/숫자/밑줄)</label>
          <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="invest"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="투자 모니터"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">DB 스키마 이름 (선택, 기본 youtube_&#123;ID&#125;)</label>
          <input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="youtube_invest"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !slug.trim() || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '생성 중...' : '생성'}
        </button>
      </div>
    </ModalShell>
  )
}

export function EditGroupModal({ onClose }: { onClose: () => void }) {
  const { activeGroup, activeSlug, reloadGroups } = useGroup()
  const [name, setName] = useState(activeGroup?.name ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await groupApi.rename(activeSlug, name.trim())
      await reloadGroups()
      onClose()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="그룹 이름 수정" onClose={onClose}>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 영문 ID (변경 불가)</label>
        <input value={activeSlug} disabled className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-gray-50 text-gray-400" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">그룹 명칭</label>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
      </div>
      <div className="flex gap-2 justify-end">
        <button onClick={onClose} className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50">취소</button>
        <button onClick={submit} disabled={busy || !name.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-60">
          {busy ? '저장 중...' : '저장'}
        </button>
      </div>
    </ModalShell>
  )
}
```
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → 에러 없음.
- [ ] **Step 3:** Commit
```bash
git add frontend/src/components/GroupModals.tsx
git commit -m "feat: 그룹 생성/이름수정 모달"
```

---

## Task 7: Layout에 설정 네비 + 그룹 버튼, App 라우트

**Files:** Modify `frontend/src/components/Layout.tsx`, `frontend/src/App.tsx`

- [ ] **Step 1:** `frontend/src/components/Layout.tsx` 수정.
  - import 추가:
    ```ts
    import { useState } from 'react'
    import { SETTING_CATEGORIES } from '../settings/defs'
    import { NewGroupModal, EditGroupModal } from './GroupModals'
    ```
  - 컴포넌트 본문에 모달 상태 추가(첫 줄들 근처):
    ```ts
    const [groupModal, setGroupModal] = useState<null | 'new' | 'edit'>(null)
    ```
  - 헤더의 그룹 셀렉터 `<select>` 다음에 버튼 2개 추가:
    ```tsx
    <button onClick={() => setGroupModal('edit')} className="text-xs px-2 py-1 border border-gray-300 rounded-lg hover:bg-gray-50">이름 수정</button>
    <button onClick={() => setGroupModal('new')} className="text-xs px-2 py-1 bg-blue-600 text-white rounded-lg hover:bg-blue-700">+ 새 그룹</button>
    ```
  - 사이드바 `<nav>`의 운영 메뉴(NAV.map) 아래에 설정 섹션을 추가:
    ```tsx
    <div className="mt-1 pt-1 border-t border-gray-100 lg:flex lg:flex-col">
      <span className="px-3 py-1 text-[11px] font-semibold text-gray-400 uppercase">설정</span>
      {SETTING_CATEGORIES.map((c) => (
        <NavLink key={c.key} to={`/g/${activeSlug}/settings/${c.key}`} className={linkClass}>
          <span>⚙️</span><span>{c.label}</span>
        </NavLink>
      ))}
    </div>
    ```
  - 최상위 반환 JSX 맨 끝(닫는 `</div>` 직전)에 모달 렌더 추가:
    ```tsx
    {groupModal === 'new' && <NewGroupModal onClose={() => setGroupModal(null)} />}
    {groupModal === 'edit' && <EditGroupModal onClose={() => setGroupModal(null)} />}
    ```
  주의: `NavLink`, `useGroup`(activeSlug), `linkClass`는 이미 Layout에 존재한다. 없으면 기존 정의를 활용.
- [ ] **Step 2:** `frontend/src/App.tsx`에 라우트 추가. import:
  ```ts
  import Settings from './pages/Settings'
  ```
  `<Route element={<Layout />}>` 자식 라우트에 추가(`logs` 다음):
  ```tsx
  <Route path="settings/:category" element={<Settings />} />
  ```
- [ ] **Step 3:** 검증 — `cd frontend && npx tsc --noEmit` → 에러 없음. `npm run build` → 성공. `git status`에 `app/static/ui/`가 stage 후보로 안 나오는지 확인(gitignored).
- [ ] **Step 4:** Commit (App.tsx와 Layout.tsx만 stage; 빌드 산출물 제외)
```bash
git add frontend/src/components/Layout.tsx frontend/src/App.tsx
git commit -m "feat: 설정 네비 섹션 + 그룹 생성/수정 버튼 + settings 라우트"
```

---

## Task 8: 전체 검증 + 빌드 게이트

**Files:** 없음(검증)

- [ ] **Step 1:** `cd frontend && npx tsc --noEmit && npx vitest run && npm run build` → tsc clean, 모든 vitest 통과(기존 7 + convert 신규), 빌드 성공.
- [ ] **Step 2:** 백엔드 무변경 확인: `cd .. && pytest -q` → 5 passed, `python -c "from app.main import app; print('ok')"` → ok.
- [ ] **Step 3:** 워킹트리 클린 확인: `git status --short` (모드 변경 아티팩트만 있으면 `git checkout -- <file>`로 정리).

---

## Task 9: 수동 통합 검증 (DB 필요)

- [ ] **Step 1:** dev 기동(`uvicorn ... 8000`, `npm run dev`) → `http://localhost:5173/app/`.
- [ ] **Step 2:** 헤더 "+ 새 그룹"으로 그룹 생성 → 새 그룹으로 이동되는지. "이름 수정"으로 명칭 변경 반영.
- [ ] **Step 3:** 좌측 설정 네비 6종 각각:
  - Database: 호스트/포트/DB/사용자 입력 + 비밀번호(저장 후 placeholder가 "설정됨"으로), sslmode 선택. 저장 후 대시보드 DB 헬스 정상.
  - AI Gateway: base_url/api_key 저장 → 모델 목록이 채워지고 primary/fallback 모델 드롭다운 선택 가능. temperature(float)/max_tokens(int).
  - Monitoring: 수집 범위(일)·확인 주기(시간) 입력값이 저장/재로딩 시 올바르게 환산(일↔시간, 시간↔분).
  - Notification: 활성화 토글, 봇 토큰(시크릿), Chat ID 추가/삭제(여러 개), parse_mode.
  - Prompts: analysis_prompt/digest_prompt 멀티라인 저장.
  - Digest: 활성화/기간/요일/시각/시간대 저장.
- [ ] **Step 4:** 그룹 2개에서 설정 격리 확인: A 그룹의 게이트웨이/프롬프트/텔레그램 값이 B와 독립.
- [ ] **Step 5:** 시크릿 보존: 비밀번호/토큰 입력 후 저장 → 다시 들어가 빈 칸으로 저장해도 기존 시크릿 유지(다른 필드만 변경 가능).

---

## Self-Review 결과 (작성자 기록)

- **스펙 커버리지**: §그룹별 설정(DB/AI게이트웨이/프롬프트/텔레그램/모니터링/다이제스트) 6종 = SETTING_DEFS(Task2) + SettingsForm(Task4) + Settings 페이지(Task5) + 네비(Task7). 그룹 생성/수정 = Task6/7. ytdb 범용 key/value API에 정확히 매핑.
- **백엔드 무변경**: 모든 설정/그룹 엔드포인트 기존 사용 — 비파괴.
- **타입/시그니처 일관성**: `settingsApi(slug).{get,put,gatewayModels}`, `SettingItem`, `FieldDef`/`SETTING_DEFS`/`SETTING_CATEGORIES`, `initialValue`/`toSaveItem`, `SettingsForm` props, `NewGroupModal`/`EditGroupModal` — Task1~7 동일 사용. `groupApi.{create,rename}`, `useGroup().{activeGroup,activeSlug,reloadGroups}`는 Plan1에서 정의됨.
- **위험**: ① `groupClient.put` 부재 가능 → Task1에서 확인 후 추가 지시. ② Task3 `toEqual`가 `is_secret`까지 비교 → 테스트 기대값에 `is_secret` 포함하도록 Step1에서 정렬 지시. ③ Layout의 `linkClass`/`NavLink`/`activeSlug` 존재 가정 → 실제 Plan1 Layout에 모두 있음(확인 지시 포함). ④ 시크릿 빈 값 저장 시 백엔드가 기존 보존(settings_manager 확인됨).
- **Plan 4 예고**: 주간 리뷰 화면, 텔레그램 수동발송(+백엔드 notify 엔드포인트), 영상별 커스텀 프롬프트(+reanalyze custom_prompt 수용).
