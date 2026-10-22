'use client'

import { Ban, Check, Mail, Phone, Reply, X } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useAsk } from '@/features/ask/useAsk'
import { AppError, call, type AskRank, type AskRequestCard } from '@/lib/api'

const RANK: Record<AskRank, { label: string; className: string } | null> = {
  unverified: { label: 'Unverified: asks for money, codes or secrecy', className: 'bg-[#fbe8ea] text-danger' },
  urgent: { label: 'Maybe urgent', className: 'bg-rose-100 text-[#6b3d06]' },
  normal: null,
  low: { label: 'A topic you silenced', className: 'bg-haze-100 text-muted' },
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

// Requests people left on the owner's "Ask my Ava" link. Everything the visitor typed is shown as plain
// text (React escapes it), never as markup or links. Renders nothing while the feature is off.
export function AskRequests({ title = 'Requests from your link', className }: { title?: string; className?: string }) {
  const { enabled, data, reload } = useAsk<{ requests: AskRequestCard[] }>('/ask-link/requests')
  const [problem, setProblem] = useState<string | null>(null)
  if (!enabled || !data) return null
  const items = data.requests

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          People who used your Ask my Ava link. Ava hasn&rsquo;t replied to them; nothing reaches them without your OK.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {problem && <Notice tone="error">{problem}</Notice>}
        {items.length === 0 ? (
          <p className="text-sm text-muted">No new requests.</p>
        ) : (
          <ul className="grid gap-3">
            {items.map((r) => (
              <RequestItem key={r.id} item={r} onChange={() => void reload()} onError={setProblem} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function RequestItem({ item, onChange, onError }: { item: AskRequestCard; onChange: () => void; onError: (m: string | null) => void }) {
  const [drafting, setDrafting] = useState(false)
  const [text, setText] = useState(`Hi ${item.name.split(' ')[0]}, thanks for getting in touch about "${item.topic}". `)
  const [busy, setBusy] = useState(false)
  const rank = RANK[item.rank]
  const waiting = item.reply?.status === 'pending'

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    onError(null)
    try {
      await fn()
      onChange()
    } catch (e) {
      onError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const act = (verb: 'dismiss' | 'spam') => run(() => call(`/ask-link/requests/${item.id}/${verb}`, { method: 'POST' }))
  const draft = () =>
    run(async () => {
      await call(`/ask-link/requests/${item.id}/reply`, { method: 'POST', json: { text } })
      setDrafting(false)
    })
  const approve = () => run(() => call(`/approvals/${item.reply?.approval_id}/approve`, { method: 'POST' }))

  return (
    <li className="rounded-2xl border border-line p-4" data-testid="ask-request">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-ink break-words">{item.name}</p>
        {rank && <Badge className={rank.className}>{rank.label}</Badge>}
        <span className="ml-auto text-xs text-muted">{when(item.created_at)}</span>
      </div>
      <p className="mt-1 text-sm text-ink break-words">
        <span className="text-muted">About: </span>
        {item.topic}
      </p>
      {item.why && <p className="mt-1 whitespace-pre-line text-sm text-body break-words">{item.why}</p>}
      <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1 break-all">
          {item.contact_kind === 'email' ? <Mail className="size-3.5" aria-hidden /> : <Phone className="size-3.5" aria-hidden />}
          {item.contact} <span className="sr-only">(not verified)</span>
        </span>
        {item.deadline && <span>Deadline {new Date(`${item.deadline}T00:00:00`).toLocaleDateString(undefined, { dateStyle: 'medium' })}</span>}
        <span>Not verified</span>
      </p>

      {waiting && (
        <div className="mt-3 rounded-xl bg-mist p-3 text-sm">
          <p className="text-muted">Your reply, waiting for your OK:</p>
          <p className="mt-1 whitespace-pre-line text-ink break-words">{item.reply?.text}</p>
          <Button size="sm" className="mt-2" disabled={busy} onClick={() => void approve()}>
            <Check aria-hidden /> Approve reply
          </Button>
        </div>
      )}

      {drafting ? (
        <div className="mt-3 grid gap-2">
          <Label htmlFor={`reply-${item.id}`}>Your reply</Label>
          <Textarea id={`reply-${item.id}`} value={text} maxLength={2000} rows={4} onChange={(e) => setText(e.target.value)} />
          <p className="text-xs text-muted">It goes to Approvals first. Superaide doesn&rsquo;t send it for you yet: once approved, send it yourself.</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy || !text.trim()} onClick={() => void draft()}>
              Save draft for approval
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setDrafting(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {!waiting && (
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setDrafting(true)}>
              <Reply aria-hidden /> Reply
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void act('dismiss')}>
            <X aria-hidden /> Dismiss
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act('spam')}>
            <Ban aria-hidden /> Mark spam
          </Button>
        </div>
      )}
    </li>
  )
}
