import { useCallback, useEffect, useState } from 'react'
import { useParams, Navigate, NavLink } from 'react-router-dom'
import { useGroup } from '../group/useGroup'
import { useAuth } from '../auth/useAuth'
import { settingsApi, type SettingItem, type PromptPreset } from '../api/settings'
import { profileApi } from '../api/profile'
import { SETTING_DEFS, visibleCategories, visibleFields } from '../settings/defs'
import SettingsForm from '../components/SettingsForm'
import DigestConfigsEditor from '../components/DigestConfigsEditor'
import DataProfilePanel from '../components/DataProfilePanel'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'

export default function Settings() {
  const { activeSlug } = useGroup()
  const { user } = useAuth()
  const { category } = useParams<{ category: string }>()
  const [items, setItems] = useState<SettingItem[]>([])
  const [models, setModels] = useState<string[]>([])
  const [presets, setPresets] = useState<PromptPreset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [modelMsg, setModelMsg] = useState<string | null>(null)
  const [recordTypes, setRecordTypes] = useState<string[]>([])

  const categories = visibleCategories(user?.role)
  const isPresetMode = user?.role !== 'admin' && category === 'prompts'
  const defs = category ? SETTING_DEFS[category] : undefined
  const isDigest = category === 'digest'
  const isDataProfile = category === 'data_profile'
  // 역할상 비허용 카테고리(URL 직접 진입)는 로드하지 않고 아래에서 리다이렉트 —
  // 비허용 요청(404)이 리다이렉트 후 도착해 에러 배너를 남기는 레이스 방지.
  const allowed = categories.some((c) => c.key === category)

  const load = useCallback(async () => {
    if (!category || (!defs && !isDigest && !isDataProfile) || !allowed) return
    if (isDataProfile) { setLoading(false); return }
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
      if (isPresetMode) {
        setPresets(await settingsApi(activeSlug).promptPresets())
      }
      if (isDigest) {
        try {
          const p = await profileApi(activeSlug).get()
          setRecordTypes((p.record_schema?.types ?? []).map((t) => t.type_key))
        } catch {
          setRecordTypes([])
        }
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [activeSlug, category, defs, isPresetMode, isDigest, isDataProfile, allowed])

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

  // 미지원 카테고리뿐 아니라 역할상 비허용 카테고리(URL 직접 진입)도 첫 허용 탭으로
  if (!category || (!defs && !isDigest && !isDataProfile) || !allowed)
    return <Navigate to={`/g/${activeSlug}/settings/${categories[0].key}`} replace />

  const label = categories.find((c) => c.key === category)?.label ?? category

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">설정 · {label}</h1>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>

      {/* 카테고리 탭: 사이드바를 1개 항목으로 줄이는 대신 여기서 전환한다. */}
      <div className="flex flex-wrap gap-1 border-b border-gray-200">
        {categories.map((c) => (
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
          {isDataProfile ? (
            <DataProfilePanel slug={activeSlug} />
          ) : isDigest ? (
            <DigestConfigsEditor items={items} saving={saving} onSave={handleSave} recordTypes={recordTypes} />
          ) : isPresetMode ? (
            <PromptPresetSelector
              key={category}
              presets={presets}
              items={items}
              saving={saving}
              onSave={handleSave}
            />
          ) : (
            <SettingsForm
              key={category}
              defs={visibleFields(user?.role, category, defs!)}
              items={items}
              models={models}
              saving={saving}
              onSave={handleSave}
            />
          )}
        </>
      )}
    </div>
  )
}

function PromptPresetSelector({
  presets, items, saving, onSave,
}: {
  presets: PromptPreset[]
  items: SettingItem[]
  saving: boolean
  onSave: (items: SettingItem[]) => void
}) {
  const current = items.find((i) => i.key === 'preset_id')?.value ?? ''
  const [selected, setSelected] = useState(current)

  const handleSave = () =>
    onSave([{ key: 'preset_id', value: selected === '' ? null : selected, value_type: 'int', is_secret: false }])

  const selectedPreset = presets.find((p) => String(p.preset_id) === selected)

  return (
    <div className="bg-white rounded-xl shadow-sm p-5 space-y-5 max-w-2xl">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">프롬프트 프리셋</label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">선택 안 함</option>
          {presets.map((p) => (
            <option key={p.preset_id} value={String(p.preset_id)}>{p.name}</option>
          ))}
        </select>
        {selectedPreset?.description && (
          <p className="text-xs text-gray-400 mt-1">{selectedPreset.description}</p>
        )}
      </div>
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
