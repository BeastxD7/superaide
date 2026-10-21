import { useState } from 'react'
import type { AuditRow, Connection } from '../api'
import { ago, describeAudit, type AuditKind } from '../format'
import { useApi } from '../useApi'

const FILTERS: { key: AuditKind | 'all'; label: string }[] = [
  { key: 'all', label: 'Everything' },
  { key: 'refusal', label: 'Refusals' },
  { key: 'identity', label: 'Identity' },
  { key: 'model', label: 'Model calls' },
  { key: 'memory', label: 'Memory' },
  { key: 'control', label: 'Approvals and controls' },
]

export function SafetyLog({ conn }: { conn: Connection }) {
  const { data, error } = useApi<AuditRow[]>(conn, '/api/audit?limit=300')
  const [filter, setFilter] = useState<AuditKind | 'all'>('all')

  if (error) return <p className="error">{error.message}</p>
  if (!data) return <p className="quiet">Loading the log…</p>

  const rows = data.map((r) => ({ row: r, ...describeAudit(r) })).filter((r) => filter === 'all' || r.kind === filter)

  return (
    <div className="log">
      <div className="filters" role="group" aria-label="Filter the log">
        {FILTERS.map((f) => (
          <button key={f.key} aria-pressed={filter === f.key} className={filter === f.key ? 'chip on' : 'chip'} onClick={() => setFilter(f.key)}>
            {f.label}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <p className="quiet">Nothing here yet.</p>
      ) : (
        <ol className="log-list">
          {rows.map(({ row, kind, text }) => (
            <li key={row.id} className={`log-row log-${kind}`}>
              <span className="log-when">{ago(row.at)}</span>
              <span className="log-text">{text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
