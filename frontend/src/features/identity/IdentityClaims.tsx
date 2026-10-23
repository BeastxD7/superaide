'use client'

import { Ban, Link2, UserCheck, UserPlus, UserX } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { call, type IdentityClaimCard, type IdentityClaimChoice } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { formatWhen } from '@/lib/dates'
import { useResource } from '@/lib/use-resource'

const CHANNEL: Record<string, string> = { telegram: 'Telegram', slack: 'Slack', web: 'The app' }

// Accounts whose name is a known person's name but that you never linked. Ava treats each as a stranger and
// asks you; only your answer here links anything. Everything shown came from the platform and the first
// message, as plain text (React escapes it). Renders nothing while the feature is off.
export function IdentityClaims({ className }: { className?: string }) {
  const { enabled, data, reload } = useResource<{ claims: IdentityClaimCard[]; relations: string[] }>('/identity-claims', {
    offOn404: true,
  })
  const [problem, setProblem] = useState<string | null>(null)
  if (!enabled || !data || data.claims.length === 0) return null

  return (
    <Card className={className} data-testid="identity-claims">
      <CardHeader>
        <CardTitle>Who is this?</CardTitle>
        <CardDescription>
          These accounts have the same name as someone you know, but you never linked them. They are treated as strangers
          and see nothing until you answer.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {problem && <Notice tone="error">{problem}</Notice>}
        <ul className="grid gap-3">
          {data.claims.map((c) => (
            <ClaimItem key={c.id} item={c} relations={data.relations} onDone={() => void reload()} onError={setProblem} />
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function ClaimItem({
  item,
  relations,
  onDone,
  onError,
}: {
  item: IdentityClaimCard
  relations: string[]
  onDone: () => void
  onError: (m: string | null) => void
}) {
  const [busy, setBusy] = useState(false)
  const [relation, setRelation] = useState('contact')
  const f = item.facts

  async function answer(choice: IdentityClaimChoice) {
    setBusy(true)
    onError(null)
    try {
      await call(`/identity-claims/${item.id}/answer`, { method: 'POST', json: { choice, relation } })
    } catch (e) {
      onError(errorMessage(e))
    } finally {
      onDone()
      setBusy(false)
    }
  }

  const rows: [string, string][] = [
    ['Name they gave', f.display_name || '(none)'],
    ['Where', CHANNEL[f.provider] ?? f.provider],
    ['Platform user id', f.platform_user_id],
    ...(f.handle ? ([['Handle', `@${f.handle.replace(/^@/, '')}`]] as [string, string][]) : []),
    ...(f.phone ? ([['Phone (from the platform)', f.phone]] as [string, string][]) : []),
    ['First message', formatWhen(f.first_message_at)],
  ]
  const contactLabel = item.choices.find((x) => x.id === 'contact')?.label

  return (
    <li className="rounded-2xl border border-line p-4" data-testid="identity-claim">
      <p className="font-medium text-ink break-words">{item.question}</p>
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="break-all text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {f.excerpt && (
        <blockquote className="mt-2 rounded-xl bg-mist p-3 text-sm text-body break-words">&ldquo;{f.excerpt}&rdquo;</blockquote>
      )}
      {item.asks > 1 && <p className="mt-1 text-xs text-muted">They wrote {item.asks} times.</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {item.contact && (
          <Button size="sm" disabled={busy} onClick={() => void answer('contact')}>
            <Link2 aria-hidden /> {contactLabel}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void answer('new')}>
          <UserPlus aria-hidden /> Yes, someone new
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void answer('stranger')}>
          <UserX aria-hidden /> No / don&rsquo;t know
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void answer('block')}>
          <Ban aria-hidden /> Block
        </Button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <UserCheck className="size-4 text-muted" aria-hidden />
        <Label htmlFor={`rel-${item.id}`} className="text-xs text-muted">
          If someone new, I know them as
        </Label>
        <select
          id={`rel-${item.id}`}
          className="rounded-md border border-line bg-white px-2 py-1 text-sm"
          value={relation}
          onChange={(e) => setRelation(e.target.value)}
        >
          {relations.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>
    </li>
  )
}
