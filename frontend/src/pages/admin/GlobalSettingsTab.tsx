import { useCallback, useEffect, useState } from 'react'
import { adminApi, type GlobalSettingItem } from '../../api/admin'

// 키 순서·구성은 서버 _GLOBAL_KEYS가 단일 출처 — 여기는 표시 라벨만 담당.
const GLOBAL_SETTING_LABELS: Record<string, { label: string; help?: string }> = {
  youtube_api_key: { label: '시스템 YouTube API 키' },
  central_poll_floor_min: { label: '중앙 폴링 하한(분)' },
  youtube_daily_quota: { label: 'YouTube 일일 쿼터' },
  ai_base_url: { label: 'AI 게이트웨이 Base URL' },
  ai_api_key: { label: 'AI 게이트웨이 API 키' },
  ai_primary_model: { label: 'AI 기본 모델' },
  ai_digest_model: { label: 'AI 다이제스트 모델' },
  ai_model_prices: { label: 'AI 모델 단가표(JSON)', help: '{"모델prefix": {"input": n, "output": n}} — $/1M 토큰' },
  telegram_bot_token: { label: '공용 텔레그램 봇 토큰' },
  db_host: { label: '기본 DB 호스트', help: '사용자 그룹 데이터 평면 폴백 DSN — 그룹에 자체 DB 설정이 없으면 이 접속을 사용' },
  db_port: { label: '기본 DB 포트' },
  db_name: { label: '기본 DB 이름' },
  db_username: { label: '기본 DB 사용자' },
  db_password: { label: '기본 DB 비밀번호' },
  db_sslmode: { label: '기본 DB sslmode' },
}

/** 일반 사용자 그룹에 적용되는 전역 기본값 편집. */
export default function GlobalSettingsTab() {
  const [items, setItems] = useState<GlobalSettingItem[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await adminApi.globalSettings()
      setItems(list)
      setValues(Object.fromEntries(list.map((i) => [i.key, i.value])))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      // 빈 값은 서버가 무시(클리어 불가)하므로 전송 자체를 생략한다.
      const payload = items
        .map((i) => ({ ...i, value: (values[i.key] ?? '').trim() }))
        .filter((i) => i.value !== '')
      const updated = await adminApi.putGlobalSettings(payload)
      setItems(updated)
      setValues(Object.fromEntries(updated.map((i) => [i.key, i.value])))
      setSavedAt(new Date().toLocaleTimeString('ko-KR'))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>
      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
      <div className="bg-white rounded-xl shadow-sm p-4 space-y-4">
        <p className="text-xs text-gray-400">
          일반 사용자 그룹에 적용되는 기본값입니다. 시크릿은 마스킹되어 표시되며, 그대로 두고 저장하면 변경되지 않습니다.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {items.map((i) => {
            const meta = GLOBAL_SETTING_LABELS[i.key] ?? { label: i.key }
            return (
              <div key={i.key} className={i.key === 'ai_model_prices' ? 'sm:col-span-2' : ''}>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {meta.label} <span className="font-normal text-gray-400 text-xs font-mono">{i.key}</span>
                </label>
                {i.key === 'ai_model_prices' ? (
                  <textarea
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    rows={3}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="미설정"
                  />
                ) : i.key === 'db_sslmode' ? (
                  <select
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">(미설정 — prefer 적용)</option>
                    <option value="disable">disable</option>
                    <option value="prefer">prefer</option>
                    <option value="require">require</option>
                  </select>
                ) : (
                  <input
                    value={values[i.key] ?? ''}
                    onChange={(e) => setValues((prev) => ({ ...prev, [i.key]: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="미설정"
                  />
                )}
                {meta.help && <p className="text-xs text-gray-400 mt-1">{meta.help}</p>}
              </div>
            )
          })}
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? '저장 중…' : '저장'}
        </button>
      </div>
    </div>
  )
}
