'use client'

import { useMemo, useState } from 'react'
import { arrayPageFetcher, type AuditRow, type Connection } from '@/features/console/api'
import { ago, describeAudit, type AuditKind } from '@/features/console/format'
import { usePoll } from '@/features/console/usePoll'
import { ListFooter } from '@/components/ui/list-footer'
import { SkeletonList } from '@/components/ui/skeleton'
import { usePaginated } from '@/lib/paginated'

const PAGE = 100

const FILTERS: { key: AuditKind | 'all'; label: string }[] = [
  { key: 'all', label: 'Everything' },
  { key: 'refusal', label: 'Refusals' },
  { key: 'identity', label: 'Identity' },
  { key: 'model', label: 'Model calls' },
  { key: 'memory', label: 'Memory' },
  { key: 'control', label: 'Approvals and controls' },
]

export function SafetyLog({ conn }: { conn: Connection }) {
  const fetcher = useMemo(() => arrayPageFetcher<AuditRow>(conn), [conn])
  const list = usePaginated<AuditRow>('/api/audit', { limit: PAGE, fetcher })
  usePoll(list, 10_000, PAGE)
  const [filter, setFilter] = useState<AuditKind | 'all'>('all')

  if (list.status === 'error' && list.failed === 'first') return <p className="error">{list.error}</p>
  if (list.status === 'loading') return <SkeletonList count={8} rowHeight={40} what="the log" />

  const rows = list.items.map((r) => ({ row: r, ...describeAudit(r) })).filter((r) => filter === 'all' || r.kind === filter)

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
      {filter !== 'all' && list.hasMore && <p className="quiet">Showing matches among the entries loaded so far. Load more to look further back.</p>}
      <ListFooter {...list.footer} endLabel="That is the whole log" />
    </div>
  )
}
