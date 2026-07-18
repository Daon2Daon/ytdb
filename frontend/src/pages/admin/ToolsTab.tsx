import { useState } from 'react'
import { adminApi, type MigrateSchemasResponse } from '../../api/admin'

/** 위험·저빈도 시스템 작업 — 별도 탭으로 분리해 오조작 방지. */
export default function ToolsTab() {
  const [migrating, setMigrating] = useState(false)
  const [migration, setMigration] = useState<MigrateSchemasResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const runMigration = async () => {
    if (migrating) return
    setMigrating(true)
    setError(null)
    try {
      setMigration(await adminApi.migrateSchemas())
    } catch (e) {
      setError(e instanceof Error ? e.message : '실행 실패')
    } finally {
      setMigrating(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="bg-white rounded-xl shadow-sm p-4 space-y-3">
        <button
          onClick={runMigration}
          disabled={migrating}
          className="bg-blue-600 text-white rounded-lg px-4 py-1.5 text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {migrating ? '실행 중…' : '전 스키마 마이그레이션 실행'}
        </button>
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>
        )}
        {migration && (
          <>
            <p className="text-sm text-gray-700">
              성공 {migration.summary.ok} · 실패 {migration.summary.failed} · 스킵 {migration.summary.skipped}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b">
                    <th className="px-3 py-2">그룹</th><th className="px-3 py-2">스키마</th>
                    <th className="px-3 py-2">상태</th><th className="px-3 py-2 text-right">소요(ms)</th>
                  </tr>
                </thead>
                <tbody>
                  {migration.results.map((r) => (
                    <tr key={r.group_id} className="border-b last:border-0">
                      <td className="px-3 py-2">{r.slug}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-600">{r.schema_name}</td>
                      <td className="px-3 py-2">
                        {r.status === 'ok' ? (
                          <span className="text-green-600">ok</span>
                        ) : r.status === 'failed' ? (
                          <span className="text-red-600">failed{r.error ? ` — ${r.error}` : ''}</span>
                        ) : (
                          <span className="text-gray-400">skipped</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-right">{r.duration_ms.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
