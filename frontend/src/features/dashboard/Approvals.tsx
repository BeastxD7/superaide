'use client'

import { Check, Phone, ShieldQuestion, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppData } from '@/features/dashboard/data'
import { call, type ApprovalItem } from '@/lib/api'
import { announceApprovalsChanged, useApprovalsChanged } from '@/lib/approvals-sync'
import { formatWhen } from '@/lib/dates'
import { errorMessage } from '@/lib/action'

const KIND_LABEL: Record<string, string> = {
  publish_post: 'Publish a post',
  message_someone_else: 'Message someone for you',
  gmail_send: 'Send an email from your Gmail',
  grant_access: 'Grant access',
  delete_memory: 'Delete something she remembers',
  ask_reply: 'Reply to someone from your Ask link',
}

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

// What Ava was asked to do that needs your yes first. Shown only while something is waiting.
export function ApprovalsCard({ name, empty = null }: { name: string; empty?: ReactNode }) {
  const { data, reload } = useAppData<ApprovalItem[]>('/approvals')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  useApprovalsChanged(reload)
  const pending = (data ?? []).filter((a) => a.status === 'pending')
  if (!pending.length) return data ? empty : null

  async function decide(id: string, verdict: 'approve' | 'reject') {
    setBusy(id)
    setError(null)
    try {
      await call(`/approvals/${id}/${verdict}`, { method: 'POST', json: { reason: reasons[id] ?? null } })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      // Read again even when it failed: "already decided" means this list was stale.
      await reload()
      announceApprovalsChanged()
      setBusy(null)
    }
  }

  return (
    <Card className="mt-6 border-violet-300">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldQuestion className="size-5 text-violet-600" aria-hidden />
          Waiting for your OK
          <Badge variant="secondary">{pending.length}</Badge>
        </CardTitle>
        <CardDescription>{name} won&apos;t do any of this until you say yes.</CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="mb-3">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
        <ul className="space-y-2">
          {pending.map((a) => (
            <li key={a.id} className="flex flex-col gap-3 rounded-xl border border-line bg-white p-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{KIND_LABEL[a.kind] ?? a.kind}</p>
                <p className="mt-0.5 break-words text-sm text-body">{a.summary}</p>
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
                {a.needs_reason && (
                  <Input
                    className="mt-2"
                    aria-label="Why is this fine? Type a short reason to approve"
                    placeholder="You have been approving fast. Type a short reason to say yes."
                    value={reasons[a.id] ?? ''}
                    onChange={(e) => setReasons({ ...reasons, [a.id]: e.target.value })}
                  />
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <Button size="sm" variant="outline" disabled={busy === a.id} onClick={() => void decide(a.id, 'reject')}>
                  <X aria-hidden /> No
                </Button>
                <Button size="sm" disabled={busy === a.id} onClick={() => void decide(a.id, 'approve')}>
                  <Check aria-hidden /> {a.origin === 'voice' ? 'Yes, confirm it here' : 'Yes, go ahead'}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
