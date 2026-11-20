'use client'

import { Check, Phone, ShieldQuestion, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { BatchCard } from '@/features/dashboard/approvals/BatchCard'
import { seenBeacon } from '@/features/dashboard/approvals/seen'
import { KIND_LABEL, useApprovalDecisions } from '@/features/dashboard/approvals/decisions'
import { useApprovals } from '@/features/dashboard/approvals/useApprovals'
import type { ApprovalItem } from '@/lib/api'
import { announceApprovalsChanged } from '@/lib/approvals-sync'
import { formatWhen } from '@/lib/dates'


// The read-back of something asked for on a voice call: exactly what was proposed, field by field, and
// the sentence that was spoken. Saying yes out loud does nothing; only the button below counts (#125).
function VoiceReadback({ readback }: { readback: NonNullable<ApprovalItem['readback']> }) {
  return (
    <div
      className="mt-2 rounded-lg border border-violet-200 bg-violet-50 p-3"
      role="group"
      aria-label="What was heard on the call"
    >
      <p className="flex items-center gap-1.5 text-xs font-medium text-violet-700">
        <Phone className="size-3.5" aria-hidden /> Heard on a call. Nothing has happened yet.
      </p>
      {readback.fields.length > 0 && (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {readback.fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-muted capitalize">{f.label}</dt>
              <dd className="min-w-0 break-words text-ink">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-2 break-words text-xs text-body">
        <span className="text-muted">Read back as: </span>&ldquo;{readback.spoken}&rdquo;
      </p>
      <p className="mt-2 text-xs text-muted">A spoken yes does not count. Tap here to confirm.</p>
    </div>
  )
}

// Everything the owner reads to decide one request: what it is, the facts, warnings, what a yes does, the sign-off, who
// asked and when. One component for the card on Home and the detail pane on the Approvals page, so the two never differ.
export function ApprovalBody({
  a,
  name,
  ticks,
  onSign,
  reasons,
  onReason,
  readOnly = false,
}: {
  a: ApprovalItem
  name: string
  ticks: Record<string, boolean>
  onSign: (sign: boolean) => void
  reasons: Record<string, string>
  onReason: (text: string) => void
  readOnly?: boolean // a decided request: no sign-off tick, no reason field, no "if you say yes"
}) {
  return (
    <div className="min-w-0 flex-1">
      <p className="text-sm font-medium text-ink">{KIND_LABEL[a.kind] ?? a.kind}</p>
      {a.fields && a.fields.length > 0 ? (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm" data-testid="approval-fields">
          {a.fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-muted">{f.label}</dt>
              <dd className="min-w-0 break-words font-medium text-ink">{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {a.warnings?.map((w) => (
        <span
          key={w}
          className="mt-1 mr-1 inline-block rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-900"
          data-testid="approval-warning"
        >
          {w}
        </span>
      ))}
      <p className="mt-0.5 break-words text-sm text-body">{a.summary}</p>
      {readOnly ? null : a.executes === false ? (
        <p
          className="mt-1 break-words rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-900"
          data-testid="approval-no-executor"
        >
          Approving this will not do anything. {a.on_approval}
        </p>
      ) : (
        a.on_approval && (
          <p className="mt-1 break-words text-xs text-muted" data-testid="approval-on-approval">
            If you say yes: {a.on_approval}
          </p>
        )
      )}
      {a.sign && !readOnly && (
        <div className="mt-2 rounded-md border border-line bg-slate-50 p-2" data-testid="approval-sign">
          <label className="flex items-start gap-2 text-sm font-medium text-ink">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0"
              checked={ticks[a.id] ?? a.sign.checked}
              disabled={!a.sign.can_change}
              onChange={(e) => onSign(e.target.checked)}
            />
            <span>{a.sign.label}</span>
          </label>
          {(ticks[a.id] ?? a.sign.checked) ? (
            <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-body" data-testid="approval-sign-text">
              {a.sign.text}
            </pre>
          ) : (
            <p className="mt-1 text-xs text-muted">Goes out in your own name, with no line from {name}.</p>
          )}
        </div>
      )}
      {a.origin === 'voice' && a.readback && <VoiceReadback readback={a.readback} />}
      {a.notes?.map((n) => (
        <p key={n} className="mt-1 break-words text-xs text-violet-700">
          {n}
        </p>
      ))}
      <p className="mt-1 text-xs text-muted">
        {a.requested_by ? `Asked by ${a.requested_by}` : a.origin === 'voice' ? 'Asked on a call' : 'Asked in a chat'} ·{' '}
        {formatWhen(a.at)}
      </p>
      {a.needs_reason && !readOnly && (
        <Input
          className="mt-2"
          aria-label="Why is this fine? Type a short reason to approve"
          placeholder="You have been approving fast. Type a short reason to say yes."
          value={reasons[a.id] ?? ''}
          onChange={(e) => onReason(e.target.value)}
        />
      )}
    </div>
  )
}

// No and Yes for one waiting request.
export function ApprovalActions({ a, busy, onDecide }: { a: ApprovalItem; busy: boolean; onDecide: (verdict: 'approve' | 'reject') => void }) {
  return (
    <div className="flex shrink-0 gap-2">
      <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide('reject')}>
        <X aria-hidden /> No
      </Button>
      <Button size="sm" disabled={busy} onClick={() => onDecide('approve')}>
        <Check aria-hidden /> {a.origin === 'voice' ? 'Yes, confirm it here' : 'Yes, go ahead'}
      </Button>
    </div>

  )
}

// What Ava was asked to do that needs your yes first. Shown only while something is waiting.
// `showExpired` (the Approvals page) also lists requests that ran out of time, so they do not just disappear.
export function ApprovalsCard({ name, empty = null, showExpired = false }: { name: string; empty?: ReactNode; showExpired?: boolean }) {
  const { data, reload, waiting: allPending, expired, batches } = useApprovals()
  const { busy, error, setError, reasons, setReasons, ticks, setSign, decide } = useApprovalDecisions(reload)
  const stale = showExpired ? expired : []
  const grouped = new Set(batches.flatMap((b) => b.items.map((i) => i.id)))
  const pending = allPending.filter((a) => !grouped.has(a.id)) // the rest, one card each
  if (!allPending.length && !stale.length) return data ? empty : null

  return (
    <Card className="mt-6 border-violet-300">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldQuestion className="size-5 text-violet-600" aria-hidden />
          {allPending.length ? 'Waiting for your OK' : 'Ran out of time'}
          <Badge variant="secondary">{allPending.length || stale.length}</Badge>
        </CardTitle>
        <CardDescription>
          {allPending.length ? `${name} won't do any of this until you say yes.` : `${name} did not do any of this. Ask her again if you still want it.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="mb-3">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
        <ul className="space-y-2">
          {batches.map((b) => (
            <BatchCard
              key={b.kind}
              batch={b}
              onError={setError}
              onDone={async () => {
                await reload()
                announceApprovalsChanged()
              }}
            />
          ))}
          {pending.map((a) => (
            <li
              key={a.id}
              ref={seenBeacon(a.id)}
              className="flex flex-col gap-3 rounded-xl border border-line bg-white p-3 sm:flex-row sm:items-center"
            >
              <ApprovalBody a={a} name={name} ticks={ticks} onSign={(v) => void setSign(a.id, v)} reasons={reasons} onReason={(v) => setReasons({ ...reasons, [a.id]: v })} />
              <ApprovalActions a={a} busy={busy === a.id} onDecide={(v) => void decide(a.id, v)} />
            </li>
          ))}
        </ul>
        {allPending.length > 0 && stale.length > 0 && (
          <h3 className="mt-4 mb-2 text-sm font-medium text-ink">Ran out of time. Ask {name} again if you still want these.</h3>
        )}
        {stale.length > 0 && (
          <ul className="space-y-2" aria-label="Requests that ran out of time">
            {stale.map((a) => (
              <li key={a.id} className="rounded-xl border border-dashed border-line bg-white/60 p-3">
                <p className="text-sm font-medium text-ink">
                  {KIND_LABEL[a.kind] ?? a.kind} <Badge variant="secondary">Expired</Badge>
                </p>
                <p className="mt-0.5 break-words text-sm text-body">{a.summary}</p>
                <p className="mt-1 text-xs text-muted">Asked {formatWhen(a.at)} · not done, it was not answered in time</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
