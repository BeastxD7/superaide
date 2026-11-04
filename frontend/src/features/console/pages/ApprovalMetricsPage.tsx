'use client'

import type { ApprovalMetrics, ApprovalWeek, Connection } from '@/features/console/api'
import { wait } from '@/features/console/format'
import { Card, type Column, DataTable, EmptyState, LoadingBlock, Notice, Stat, Stats } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

const pct = (x: number | null) => (x === null ? '–' : `${Math.round(x * 100)}%`)
const num = (n: number) => <span className="num">{n}</span>

const COLUMNS: Column<ApprovalWeek>[] = [
  { key: 'week', header: 'Week of', render: (w) => w.week },
  { key: 'asked', header: 'Asked', align: 'right', render: (w) => num(w.requested_total) },
  { key: 'decided', header: 'Answered', align: 'right', render: (w) => num(w.decided) },
  { key: 'median', header: 'Median wait', align: 'right', render: (w) => <span className="num">{wait(w.median_s)}</span> },
  { key: 'p90', header: 'p90 wait', align: 'right', render: (w) => <span className="num">{wait(w.p90_s)}</span> },
  { key: 'stamp', header: 'Unread or under 2 s', align: 'right', render: (w) => <span className="num">{pct(w.rubber_stamp_share)}</span> },
  { key: 'rej', header: 'No rate', align: 'right', render: (w) => <span className="num">{pct(w.reject_rate)}</span> },
  { key: 'brake', header: 'Brake held', align: 'right', render: (w) => num(w.brake_held) },
  { key: 'exp', header: 'Expired', align: 'right', render: (w) => num(w.expired) },
]

export function ApprovalMetricsPage({ conn }: { conn: Connection }) {
  const { data, error } = useApi<ApprovalMetrics>(conn, '/api/approval-metrics?weeks=12', 15000)
  if (error) return <Notice tone={error.status === 404 ? 'info' : 'danger'}>{error.status === 404 ? 'Approval metrics are switched off (APPROVAL_METRICS_ENABLED).' : error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading approval metrics…" />
  const t = data.totals
  return (
    <>
      <Card description={`The last ${data.window_weeks} weeks for this owner. A request is "unread" when it was answered without opening the card, or within ${data.quick_ms / 1000} seconds of opening it. Only counts and times are stored: no card text, recipient or name.`}>
        <Stats cols={4} label="Approval summary">
          <Stat label="Asked a week" value={t.per_week} />
          <Stat label="Median wait" value={wait(t.median_s)} />
          <Stat label="Unread or under 2 s" value={pct(t.rubber_stamp_share)} tone={t.rubber_stamp_share !== null && t.rubber_stamp_share > 0.5 ? 'warn' : 'neutral'} />
          <Stat label="No rate" value={pct(t.reject_rate)} />
        </Stats>
      </Card>
      <Card title="Week by week" titleId="weeks-title">
        <DataTable
          caption="Approval requests and answers per week, newest first"
          columns={COLUMNS}
          rows={data.weeks}
          rowKey={(w) => w.week}
          empty={<EmptyState title="No approval requests yet">When Ava asks for an OK, the weekly numbers appear here.</EmptyState>}
        />
      </Card>
    </>
  )
}
