'use client'

import { useState } from 'react'
import type { AuditRow, Connection } from '@/features/console/api'
import { ago, describeAudit, type AuditKind } from '@/features/console/format'
import { Badge, Card, Chip, EmptyState, LoadingBlock, Notice, Toolbar, type Tone } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

const FILTERS: { key: AuditKind | 'all'; label: string }[] = [
  { key: 'all', label: 'Everything' },
  { key: 'refusal', label: 'Refusals' },
  { key: 'identity', label: 'Identity' },
  { key: 'model', label: 'Model calls' },
  { key: 'memory', label: 'Memory' },
  { key: 'control', label: 'Approvals and controls' },
]

const KIND: Record<AuditKind, { tone: Tone; label: string }> = {
  refusal: { tone: 'danger', label: 'Refusal' },
  identity: { tone: 'accent', label: 'Identity' },
  model: { tone: 'neutral', label: 'Model' },
  memory: { tone: 'neutral', label: 'Memory' },
  control: { tone: 'warn', label: 'Control' },
}

export function SafetyLog({ conn }: { conn: Connection }) {
  const { data, error } = useApi<AuditRow[]>(conn, '/api/audit?limit=300')
  const [filter, setFilter] = useState<AuditKind | 'all'>('all')

  if (error) return <Notice tone="danger">{error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading the log…" />

  const rows = data.map((r) => ({ row: r, ...describeAudit(r) })).filter((r) => filter === 'all' || r.kind === filter)

  return (
    <Card>
      <Toolbar label="Filter the log">
        {FILTERS.map((f) => (
          <Chip key={f.key} pressed={filter === f.key} onClick={() => setFilter(f.key)}>
            {f.label}
          </Chip>
        ))}
      </Toolbar>
      {rows.length === 0 ? (
        <EmptyState title="Nothing here yet.">Entries appear here as they happen.</EmptyState>
      ) : (
        <div className="c-table-wrap" tabIndex={0} role="region" aria-label="Safety log entries" style={{ maxHeight: 640 }}>
          <ol className="log-list">
            {rows.map(({ row, kind, text }) => (
              <li key={row.id} className={`log-row log-${kind}`}>
                <span className="log-when">{ago(row.at)}</span>
                <Badge tone={KIND[kind].tone}>{KIND[kind].label}</Badge>
                <span className="log-text">{text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  )
}
