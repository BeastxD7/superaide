'use client'

import type { Connection, Spend, SpendEntry } from '@/features/console/api'
import { ago, usd } from '@/features/console/format'
import { Card, type Column, DataTable, EmptyState, LoadingBlock, Meter, Notice, Stat, Stats } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

const COLUMNS: Column<SpendEntry>[] = [
  { key: 'when', header: 'When', render: (e) => <span className="quiet">{ago(e.at)}</span> },
  { key: 'purpose', header: 'Purpose', render: (e) => e.purpose.replaceAll('_', ' ') },
  { key: 'model', header: 'Model', render: (e) => <code>{e.model}</code> },
  { key: 'in', header: 'Input', align: 'right', render: (e) => <span className="num">{e.input_tokens.toLocaleString()}</span> },
  { key: 'cached', header: 'Cached', align: 'right', render: (e) => <span className="num">{e.cache_read_tokens.toLocaleString()}</span> },
  { key: 'out', header: 'Output', align: 'right', render: (e) => <span className="num">{e.output_tokens.toLocaleString()}</span> },
  { key: 'cost', header: 'Cost', align: 'right', render: (e) => <span className="num">{e.status === 'settled' ? usd(e.cost_usd, 5) : e.status}</span> },
]

export function SpendPage({ conn }: { conn: Connection }) {
  const { data, error } = useApi<{ summary: Spend; entries: SpendEntry[] }>(conn, '/api/spend')
  if (error) return <Notice tone="danger">{error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading spend…" />
  const { summary, entries } = data
  return (
    <>
      <Card
        description={
          <>
            {usd(summary.settled_usd)} spent of a {usd(summary.cap_usd, 2)} cap across {summary.calls} calls. {usd(summary.remaining_usd)} left. Each call
            reserves its worst-case price first and is refused if that could pass the cap.
          </>
        }
      >
        <Stats cols={4} label="Spend summary">
          <Stat label="Spent" value={usd(summary.settled_usd, 2)} />
          <Stat label="Cap" value={usd(summary.cap_usd, 2)} />
          <Stat label="Left" value={usd(summary.remaining_usd, 2)} />
          <Stat label="Calls" value={summary.calls} />
        </Stats>
        <Meter label="Spend against the cap" value={summary.settled_usd} max={summary.cap_usd} />
      </Card>
      <Card title="Model calls" titleId="calls-title">
        <DataTable
          caption="Model calls, newest first"
          columns={COLUMNS}
          rows={entries}
          rowKey={(e) => e.id}
          rowClass={(e) => (e.status !== 'settled' ? 'is-muted' : undefined)}
          maxHeight={560}
          empty={
            <EmptyState title="No model calls yet">
              Run <code>uv run superaide hello</code> for one tiny real call (well under a cent) to check your API key.
            </EmptyState>
          }
        />
      </Card>
    </>
  )
}
