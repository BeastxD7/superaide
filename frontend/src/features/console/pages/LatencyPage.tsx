'use client'

import { useState } from 'react'
import type { Connection, Latency, LatencyScope } from '@/features/console/api'
import { ms } from '@/features/console/format'
import { Card, type Column, DataTable, EmptyState, LoadingBlock, Notice, Stat, Stats } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

const WINDOWS = [
  { hours: 1, label: 'Last hour' },
  { hours: 24, label: 'Last day' },
  { hours: 168, label: 'Last week' },
]

const STAGE_LABEL: Record<string, string> = {
  total: 'Whole turn',
  verify: 'Verify',
  identity: 'Who is it',
  gate: 'Audience gate',
  retrieval: 'Recall and history',
  airlock: 'Airlock',
  model: 'Model',
  policy: 'Policy check',
  reply: 'Reply bookkeeping',
  send: 'Channel send',
  first_token: 'First word (streaming)',
}

type Row = { stage: string }

function scopeColumns(title: string, pick: (d: Latency) => LatencyScope | undefined, data: Latency): Column<Row>[] {
  const cell = (r: Row) => pick(data)?.stages[r.stage]
  return [
    { key: `${title}-p50`, header: `${title} p50`, align: 'right', render: (r) => <span className="num">{ms(cell(r)?.p50)}</span> },
    { key: `${title}-p95`, header: `${title} p95`, align: 'right', render: (r) => <span className="num">{ms(cell(r)?.p95)}</span> },
  ]
}

export function LatencyPage({ conn }: { conn: Connection }) {
  const [hours, setHours] = useState(24)
  const { data, error } = useApi<Latency>(conn, `/api/latency?hours=${hours}`, 15000)
  if (error) return <Notice tone="danger">{error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading latency…" />
  const stages = ['total', ...data.stage_order, 'first_token']
  const rows: Row[] = stages.map((stage) => ({ stage }))
  const columns: Column<Row>[] = [
    { key: 'stage', header: 'Step', render: (r) => STAGE_LABEL[r.stage] ?? r.stage },
    { key: 'n', header: 'Turns that ran it', align: 'right', render: (r) => <span className="num">{data.overall.stages[r.stage]?.n ?? 0}</span> },
    ...scopeColumns('All', (d) => d.overall, data),
    ...data.channels.flatMap((c) => scopeColumns(c.provider, (d) => d.channels.find((x) => x.provider === c.provider), data)),
  ]
  const total = data.overall.stages.total
  return (
    <>
      <Card description="Time from a message arriving to the reply going out, step by step. p50 is the typical turn, p95 the slow one in twenty. Only numbers are kept: no message text, no names.">
        <div role="group" aria-label="Time window" className="chips">
          {WINDOWS.map((w) => (
            <button key={w.hours} aria-pressed={hours === w.hours} className={hours === w.hours ? 'chip on' : 'chip'} onClick={() => setHours(w.hours)}>
              {w.label}
            </button>
          ))}
        </div>
        <Stats cols={3} label="Latency summary">
          <Stat label="Turns" value={data.overall.turns} />
          <Stat label="Typical turn (p50)" value={ms(total?.p50)} />
          <Stat label="Slow turn (p95)" value={ms(total?.p95)} />
        </Stats>
      </Card>
      <Card title="Each step" titleId="steps-title">
        <DataTable
          caption="Milliseconds per step of a turn, for all channels and for each channel"
          columns={columns}
          rows={data.overall.turns === 0 ? [] : rows}
          rowKey={(r) => r.stage}
          empty={<EmptyState title="No turns in this window">Send Ava a message and the timings appear here.</EmptyState>}
        />
      </Card>
    </>
  )
}
