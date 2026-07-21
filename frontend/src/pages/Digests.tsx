import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import dayjs from 'dayjs'
import { useGroup } from '../group/useGroup'
import { digestApi } from '../api/digests'
import { settingsApi } from '../api/settings'
import { profileApi, type GroupProfile } from '../api/profile'
import type { Digest, DigestScheduleConfig } from '../api/types'
import Spinner from '../components/Spinner'
import ErrorBanner from '../components/ErrorBanner'
import ProfileCard from '../components/ProfileCard'

function periodBadge(d: Digest): string {
  const days = d.period_days ?? (d.period_weeks > 0 ? d.period_weeks * 7 : 7)
  if (days === 1) return '일간'
  if (days === 30) return '월간'
  return '주간'
}

function parseConfigsFromSettings(items: { key: string; value: string | null }[]): DigestScheduleConfig[] {
  const raw = items.find((i) => i.key === 'configs')?.value
  if (raw) {
    try {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr) && arr.length) return arr as DigestScheduleConfig[]
    } catch { /* fall through */ }
  }
  const map: Record<string, string> = {}
  items.forEach((i) => { map[i.key] = i.value ?? '' })
  if (!map.enabled && !map.period_weeks) return []
  return [{
    id: 'legacy',
    name: '다이제스트',
    enabled: String(map.enabled).toLowerCase() === 'true',
    period_days: 7,
    schedule_time: map.schedule_time || '20:00',
    schedule_day: map.schedule_day || 'sun',
    schedule_dom: 1,
    timezone: map.timezone || 'Asia/Seoul',
    category: map.category || '',
    digest_prompt: '',
    telegram_enabled: String(map.telegram_enabled).toLowerCase() === 'true',
  }]
}

export default function Digests() {
  const { activeSlug } = useGroup()
  const [items, setItems] = useState<Digest[]>([])
  const [configs, setConfigs] = useState<DigestScheduleConfig[]>([])
  const [selectedConfigId, setSelectedConfigId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [profile, setProfile] = useState<GroupProfile | null>(null)
  const [regenerating, setRegenerating] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [digests, settings] = await Promise.all([
        digestApi(activeSlug).list(),
        settingsApi(activeSlug).get('digest'),
      ])
      setItems(digests)
      const parsed = parseConfigsFromSettings(settings)
      setConfigs(parsed)
      if (parsed.length && !selectedConfigId) {
        setSelectedConfigId(parsed[0].id)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeSlug])

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const p = await profileApi(activeSlug).get()
        setProfile(p)
      } catch {
        setProfile(null)
      }
    }
    loadProfile()
  }, [activeSlug])

  const handleRegenerate = async () => {
    setRegenerating(true)
    try {
      const p = await profileApi(activeSlug).regenerate()
      setProfile(p)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setRegenerating(false)
    }
  }

  const canGenerate = configs.length > 0

  const handleGenerate = async () => {
    if (!canGenerate) {
      alert('설정 > 리뷰 알림에서 digest 설정을 먼저 추가하세요.')
      return
    }
    setGenerating(true)
    try {
      await digestApi(activeSlug).generate(selectedConfigId || configs[0]?.id)
      await load()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const handleDelete = async (pk: number) => {
    if (!window.confirm('이 리뷰를 삭제할까요?')) return
    try {
      await digestApi(activeSlug).remove(pk)
      setItems((prev) => prev.filter((d) => d.digest_pk !== pk))
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const configOptions = useMemo(
    () => configs.map((c) => ({ id: c.id, label: c.name || c.id })),
    [configs],
  )

  if (loading) return <Spinner />

  return (
    <div className="space-y-4">
      {profile && (
        <ProfileCard
          sections={profile.digest_sections}
          status={profile.bootstrap_status}
          onRegenerate={handleRegenerate}
          regenerating={regenerating}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">리뷰 알림</h1>
        <div className="flex items-center gap-2">
          {canGenerate && (
            <select
              className="border border-gray-300 rounded-lg px-2 py-2 text-sm"
              value={selectedConfigId}
              onChange={(e) => setSelectedConfigId(e.target.value)}
            >
              {configOptions.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          )}
          <button onClick={handleGenerate} disabled={generating || !canGenerate}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60">
            {generating ? '생성 중...' : '지금 생성'}
          </button>
        </div>
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {items.length === 0 ? (
        <div className="bg-white rounded-xl shadow-sm py-16 text-center text-gray-400">
          <p className="text-5xl mb-3">📊</p>
          <p>리뷰가 없습니다. 설정에서 digest를 추가한 뒤 &quot;지금 생성&quot;으로 만들어 보세요.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((d) => (
            <div key={d.digest_pk} className="bg-white rounded-xl shadow-sm p-4 flex items-center gap-4">
              <Link to={`/g/${activeSlug}/digests/${d.digest_pk}`} className="flex-1 min-w-0">
                <p className="font-medium text-gray-900 truncate">
                  {d.config_name ? `[${d.config_name}] ` : `[${periodBadge(d)}] `}
                  {d.headline || '리뷰'}
                </p>
                <p className="text-xs text-gray-500 mt-0.5">
                  {dayjs(d.period_start).format('YYYY-MM-DD')} ~ {dayjs(d.period_end).format('YYYY-MM-DD')}
                  {' · '}영상 {d.video_count}건 · {d.status}
                </p>
              </Link>
              <button onClick={() => handleDelete(d.digest_pk)}
                className="px-2.5 py-1.5 text-xs rounded bg-red-50 text-red-500 hover:bg-red-100 shrink-0">삭제</button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
