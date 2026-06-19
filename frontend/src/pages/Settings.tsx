import { useCallback, useEffect, useState } from 'react'
import { useParams, Navigate, NavLink } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { settingsApi, type SettingItem } from '../api/settings'
import { SETTING_DEFS, SETTING_CATEGORIES } from '../settings/defs'
import SettingsForm from '../components/SettingsForm'
import DigestConfigsEditor from '../components/DigestConfigsEditor'
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
  const isDigest = category === 'digest'

  const load = useCallback(async () => {
    if (!category || (!defs && !isDigest)) return
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
  }, [activeSlug, category, defs])

  useEffect(() => { load() }, [load])

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

  if (!category || (!defs && !isDigest)) return <Navigate to={`/g/${activeSlug}/settings/${SETTING_CATEGORIES[0].key}`} replace />

  const label = SETTING_CATEGORIES.find((c) => c.key === category)?.label ?? category

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">설정 · {label}</h1>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>

      {/* 카테고리 탭: 사이드바를 1개 항목으로 줄이는 대신 여기서 전환한다. */}
      <div className="flex flex-wrap gap-1 border-b border-gray-200">
        {SETTING_CATEGORIES.map((c) => (
          <NavLink
            key={c.key}
            to={`/g/${activeSlug}/settings/${c.key}`}
            className={({ isActive }) =>
              `px-3 py-2 text-sm rounded-t-lg -mb-px border-b-2 transition-colors ${
                isActive
                  ? 'border-blue-600 text-blue-600 font-medium'
                  : 'border-transparent text-gray-500 hover:text-gray-800'
              }`
            }
          >
            {c.label}
          </NavLink>
        ))}
      </div>

      {error && <ErrorBanner message={error} onRetry={load} />}
      {loading ? (
        <Spinner />
      ) : (
        <>
          {category === 'ai_gateway' && modelMsg && (
            <div className="text-xs text-orange-600 bg-orange-50 border border-orange-200 rounded-lg px-3 py-2">
              모델 목록을 불러오지 못했습니다: {modelMsg} (base_url/api_key 저장 후 다시 시도)
            </div>
          )}
          {isDigest ? (
            <DigestConfigsEditor items={items} saving={saving} onSave={handleSave} />
          ) : (
            <SettingsForm key={category} defs={defs!} items={items} models={models} saving={saving} onSave={handleSave} />
          )}
        </>
      )}
    </div>
  )
}
