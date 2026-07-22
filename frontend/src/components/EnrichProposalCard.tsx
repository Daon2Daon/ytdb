import { useState } from 'react'
import { profileApi, type EnrichProposal, type GroupProfile } from '../api/profile'
import { proposalSummary } from './DataProfile.logic'

interface Props {
  slug: string
  proposal: EnrichProposal
  onApplied: (p: GroupProfile) => void
}

export default function EnrichProposalCard({ slug, proposal, onApplied }: Props) {
  const [busy, setBusy] = useState(false)
  const lines = proposalSummary(proposal)
  if (!lines.length) return null

  const act = async (fn: () => Promise<GroupProfile>) => {
    setBusy(true)
    try {
      onApplied(await fn())
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 space-y-2">
      <h2 className="font-semibold text-blue-900">프로필 보강 제안</h2>
      {proposal.note && <p className="text-sm text-blue-800">{proposal.note}</p>}
      <ul className="text-sm text-blue-800 list-disc pl-5">
        {lines.map((l) => <li key={l}>{l}</li>)}
      </ul>
      <div className="flex gap-2">
        <button type="button" disabled={busy}
          onClick={() => act(() => profileApi(slug).applyProposal())}
          className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
          적용
        </button>
        <button type="button" disabled={busy}
          onClick={() => act(() => profileApi(slug).dismissProposal())}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50">
          무시
        </button>
      </div>
    </div>
  )
}
