import { useCallback, useEffect, useState } from 'react'
import { profileApi, type GroupProfile, type RecordSchema, type VocabAxis } from '../api/profile'
import type { DigestSection } from '../api/types'
import DigestSectionBuilder from './DigestSectionBuilder'
import RecordSchemaBuilder from './RecordSchemaBuilder'
import VocabEditor from './VocabEditor'
import Spinner from './Spinner'
import ErrorBanner from './ErrorBanner'
import EnrichProposalCard from './EnrichProposalCard'
import MergeQueue from './MergeQueue'

interface Props {
  slug: string
}

export default function DataProfilePanel({ slug }: Props) {
  const [profile, setProfile] = useState<GroupProfile | null>(null)
  const [persona, setPersona] = useState('')
  const [sections, setSections] = useState<DigestSection[]>([])
  const [schema, setSchema] = useState<RecordSchema>({ version: 1, types: [] })
  const [vocab, setVocab] = useState<Record<string, VocabAxis>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const applyLoaded = (p: GroupProfile) => {
    setProfile(p)
    setPersona(p.persona ?? '')
    setSections(p.digest_sections ?? [])
    setSchema(p.record_schema ?? { version: 1, types: [] })
    setVocab(p.vocab ?? {})
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      applyLoaded(await profileApi(slug).get())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => { load() }, [load])

  const save = async () => {
    setSaving(true)
    try {
      applyLoaded(await profileApi(slug).put({
        persona, digest_sections: sections, record_schema: schema, vocab,
      }))
      setSavedAt(new Date().toLocaleTimeString('ko-KR'))
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <Spinner />
  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!profile) return null

  const recordTypes = schema.types.map((t) => t.type_key)

  return (
    <div className="space-y-5 max-w-3xl">
      {profile.enrich_proposal && Object.keys(profile.enrich_proposal).length > 0 && (
        <EnrichProposalCard slug={slug} proposal={profile.enrich_proposal} onApplied={applyLoaded} />
      )}

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-2">
        <h2 className="font-semibold text-gray-800">페르소나</h2>
        <textarea
          className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm"
          rows={2}
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          placeholder="이 그룹 리포트를 쓰는 애널리스트를 한 문장으로"
        />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">리포트 섹션</h2>
        <DigestSectionBuilder sections={sections} onChange={setSections} recordTypes={recordTypes} />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">
          레코드 스키마 <span className="text-xs text-gray-400">v{schema.version}</span>
        </h2>
        <RecordSchemaBuilder schema={schema} onChange={setSchema} />
      </div>

      <div className="bg-white rounded-xl shadow-sm p-5 space-y-3">
        <h2 className="font-semibold text-gray-800">통제 어휘</h2>
        <VocabEditor vocab={vocab} onChange={setVocab} />
        {(profile.vocab_pending?.length ?? 0) > 0 && (
          <p className="text-xs text-amber-600">
            미매핑 값: {profile.vocab_pending!.join(', ')}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button type="button" onClick={save} disabled={saving}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          {saving ? '저장 중...' : '저장'}
        </button>
        {savedAt && <span className="text-xs text-green-600">저장됨 {savedAt}</span>}
      </div>

      <MergeQueue slug={slug} />
    </div>
  )
}
