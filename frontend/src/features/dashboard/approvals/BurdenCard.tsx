'use client'

import { Gauge } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppData } from '@/features/dashboard/data'

type Burden = {
  window_weeks: number
  quick_ms: number
  brake_enabled: boolean
  totals: {
    requested_total: number
    per_week: number
    requested_by_kind: Record<string, number>
    decided: number
    approved: number
    rejected: number
    expired: number
    median_s: number | null
    p90_s: number | null
    rubber_stamp_share: number | null
    reject_rate: number | null
    brake_held: number
  }
}

const pct = (x: number | null) => (x === null ? 'n/a' : `${Math.round(x * 100)}%`)
const secs = (s: number | null) => (s === null ? 'n/a' : s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`)

const KIND: Record<string, string> = {
  publish_post: 'Publish a post',
  message_someone_else: 'Message someone',
  gmail_send: 'Send an email',
  grant_access: 'Grant access',
  delete_memory: 'Forget something',
  ask_reply: 'Reply from your Ask link',
}

// "How much am I being asked?" (#407): counts and timings of your own approvals. Nothing on a card is kept.
export function BurdenCard() {
  const { data, error } = useAppData<Burden>('/approvals/burden')
  if (error || !data) return null // off (404) or not loaded: say nothing
  const t = data.totals
  if (t.requested_total === 0) {
    return (
      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-5 text-violet-600" aria-hidden />
            How much am I being asked?
          </CardTitle>
          <CardDescription>Nothing yet. Once Ava asks for your OK, how often and how quickly you answer shows up here.</CardDescription>
        </CardHeader>
      </Card>
    )
  }
  const kinds = Object.entries(t.requested_by_kind).sort((a, b) => b[1] - a[1])
  return (
    <Card className="mt-6" data-testid="burden-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-5 text-violet-600" aria-hidden />
          How much am I being asked?
        </CardTitle>
        <CardDescription>
          The last {data.window_weeks} weeks. Only counts and times are kept, never what a request said or who it was for.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm text-body">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Asked" value={`${t.requested_total}`} note={`${t.per_week} a week`} />
          <Fact label="Typical time to answer" value={secs(t.median_s)} note={`slowest tenth: ${secs(t.p90_s)}`} />
          <Fact label="Said yes / no" value={`${t.approved} / ${t.rejected}`} note={`no rate ${pct(t.reject_rate)}`} />
          <Fact label="Ran out of time" value={`${t.expired}`} note="never answered" />
        </dl>
        <p>
          <strong className="font-medium text-ink">{pct(t.rubber_stamp_share)}</strong> of your answers came without opening the request or within{' '}
          {data.quick_ms / 1000} seconds of opening it. If that is high, ask Ava to ask less, or let her handle the routine ones.
          {data.brake_enabled && t.brake_held > 0 && <> The slow-down for fast yeses held {t.brake_held} of them.</>}
        </p>
        {kinds.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Requests by kind">
            {kinds.map(([kind, n]) => (
              <li key={kind} className="rounded-full border border-line px-2 py-0.5 text-xs">
                {KIND[kind] ?? kind}: {n}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function Fact({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="text-lg font-medium text-ink">{value}</dd>
      <dd className="text-xs text-muted">{note}</dd>
    </div>
  )
}
