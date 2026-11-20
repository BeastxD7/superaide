'use client'

import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useState } from 'react'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { ActionError, LoadError, Loading } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { CATEGORY_LABEL, CHANNEL_LABEL, capitalise } from '@/features/dashboard/data'
import { errorMessage } from '@/lib/action'
import { call, type ContactFact, type ContactRecord, type FriendTier } from '@/lib/api'
import { formatDay } from '@/lib/dates'
import { useResource } from '@/lib/use-resource'
import { useSession } from '@/lib/session-context'

// One person: what Ava knows about them, where each thing came from and who may be told it. The owner edits the
// relationship, VIP and private notes, and can forget a fact (Ava stops using it; the row is kept, retired).
const TIER_LABEL: Record<FriendTier, string> = {
  colleague: 'Colleague',
  verified_friend: 'Verified friend',
  unverified_friend: 'Unverified: waiting for your approval',
  not_linked: 'Not linked yet',
}

export function ContactPage() {
  const { id } = useParams<{ id: string }>()
  const { me } = useSession()
  const { data, error, reload } = useResource<ContactRecord>(`/people/${id}`)
  const name = me?.agent.name ?? 'Ava'
  return (
    <div className="max-w-3xl py-4">
        <Link href="/dashboard/people" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink @3xl/md:hidden">
          <ArrowLeft className="size-4" aria-hidden /> People
        </Link>
        {error && !data ? (
          <div className="mt-6">
            <LoadError what="this person" error={error} onRetry={reload} />
          </div>
        ) : !data ? (
          <Loading />
        ) : (
          <Record key={data.id} record={data} assistant={name} reload={reload} />
        )}
    </div>
  )
}

function Record({ record, assistant, reload }: { record: ContactRecord; assistant: string; reload: () => Promise<unknown> }) {
  const [relationship, setRelationship] = useState(record.relationship)
  const [notes, setNotes] = useState(record.notes)
  const [vip, setVip] = useState(record.vip)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const dirty = relationship !== record.relationship || notes !== record.notes

  async function save(patch: Partial<Pick<ContactRecord, 'relationship' | 'vip' | 'notes'>>) {
    setSaving(true)
    setSaved(false)
    setActionError(null)
    try {
      await call(`/people/${record.id}`, { method: 'PATCH', json: patch })
      await reload()
      setSaved(true)
    } catch (e) {
      setActionError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  async function forget(fact: ContactFact) {
    setActionError(null)
    try {
      await call(`/people/${record.id}/facts/${fact.id}/forget`, { method: 'POST' })
    } catch (e) {
      setActionError(errorMessage(e))
    }
    setConfirm(null)
    await reload()
  }

  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <h1 className="text-3xl font-medium text-ink tight [overflow-wrap:anywhere]">{record.name}</h1>
        {record.vip && <Badge>VIP</Badge>}
        <Badge className="font-normal" data-tier={record.tier}>
          {TIER_LABEL[record.tier]}
        </Badge>
      </div>
      <p className="mt-1 text-body">
        Everything {assistant} holds about {record.name}, where it came from and who may be told it. Forgetting a fact stops{' '}
        {assistant} using it.
      </p>

      <section className="mt-6 rounded-xl border border-line bg-white p-4" aria-labelledby="where">
        <h2 id="where" className="text-sm font-medium text-ink">
          Recognised on
        </h2>
        {record.channels.length ? (
          <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm text-body">
            {record.channels.map((c) => (
              <li key={c.provider} className="inline-flex items-center gap-1.5">
                {isBrand(c.provider) && <BrandIcon brand={c.provider} size={14} decorative />}
                {CHANNEL_LABEL[c.provider] ?? capitalise(c.provider)}
                {c.handle && <span className="text-muted">@{c.handle}</span>}
                {c.last_seen_at && <span className="text-muted">last seen {formatDay(c.last_seen_at)}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted">Not linked yet. {assistant} only recognises someone from a linked account, never a name.</p>
        )}
      </section>

      <Accounts record={record} assistant={assistant} reload={reload} />

      <section className="mt-4 rounded-xl border border-line bg-white p-4" aria-labelledby="yours">
        <h2 id="yours" className="text-sm font-medium text-ink">
          Your details
        </h2>
        <div className="mt-3 grid gap-4">
          <div>
            <Label htmlFor="relationship">Relationship</Label>
            <Input id="relationship" value={relationship} maxLength={30} onChange={(e) => setRelationship(e.target.value)} className="mt-1" />
          </div>
          <div>
            <Label htmlFor="notes">Your private notes</Label>
            <Textarea id="notes" value={notes} maxLength={2000} rows={3} onChange={(e) => setNotes(e.target.value)} className="mt-1" />
            <p className="mt-1 text-xs text-muted">Only you see these. {assistant} does not tell them to anyone.</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-body">
            <input
              type="checkbox"
              checked={vip}
              disabled={saving}
              onChange={(e) => {
                setVip(e.target.checked)
                void save({ vip: e.target.checked })
              }}
              className="size-4 accent-violet-600"
            />
            VIP: always gets through to you, even in quiet hours
          </label>
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={!dirty || saving || !relationship.trim()} onClick={() => void save({ relationship, notes })}>
              Save
            </Button>
            {saved && !dirty && (
              <span role="status" className="text-xs text-muted">
                Saved
              </span>
            )}
          </div>
        </div>
      </section>

      <h2 className="mt-8 text-lg font-medium text-ink">What {assistant} knows</h2>
      <ActionError action="save that" error={actionError} />
      {record.facts.length === 0 ? (
        <p className="mt-2 rounded-xl border border-dashed border-line p-4 text-sm text-muted">
          {assistant} holds nothing about {record.name} yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {record.facts.map((f) => (
            <li key={f.id} className="rounded-xl border border-line bg-white p-4">
              <p className="text-ink [overflow-wrap:anywhere]">{f.content}</p>
              <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                <span>{CATEGORY_LABEL[f.category] ?? capitalise(f.category)}</span>
                <span>
                  {f.source_label}
                  {f.source_title ? `: ${f.source_title}` : ''}, {formatDay(f.learned_at)}
                </span>
                <Badge className="font-normal" data-audience={f.audience.kind}>
                  Shared with: {f.audience.label}
                </Badge>
              </p>
              {confirm === f.id ? (
                <div className="mt-3 flex items-center gap-2 text-xs">
                  <span className="text-muted">{assistant} won&apos;t use this again.</span>
                  <Button size="sm" variant="destructive" onClick={() => void forget(f)}>
                    Forget
                  </Button>
                  <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirm(null)}>
                    Keep
                  </Button>
                </div>
              ) : (
                <button onClick={() => setConfirm(f.id)} className="mt-2 inline-flex touch:min-h-11 items-center text-xs font-medium text-muted hover:text-danger">
                  Forget this
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

// Accounts the owner lists for this person, and a one-time invite link. A username only makes the sender an
// unverified friend the owner approves; an id typed here is the owner's own approval.
function Accounts({ record, assistant, reload }: { record: ContactRecord; assistant: string; reload: () => Promise<unknown> }) {
  const [provider, setProvider] = useState('telegram')
  const [handle, setHandle] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ available: boolean; url?: string; expires_days?: number } | null>(null)

  async function run(fn: () => Promise<unknown>) {
    setError(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <section className="mt-4 rounded-xl border border-line bg-white p-4" aria-labelledby="accounts">
      <h2 id="accounts" className="text-sm font-medium text-ink">
        Their accounts
      </h2>
      <p className="mt-1 text-xs text-muted">
        List an account and {assistant} acknowledges it, but only shares what is public until you approve it. A name in a
        message never counts.
      </p>
      {record.handles.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-body">
          {record.handles.map((h) => (
            <li key={h.id} className="flex items-center gap-3">
              <span>
                {CHANNEL_LABEL[h.provider] ?? capitalise(h.provider)} {h.kind === 'id' ? `id ${h.value}` : `@${h.value}`}
              </span>
              <button
                className="touch:min-h-11 text-xs font-medium text-muted hover:text-danger"
                onClick={() => void run(() => call(`/people/${record.id}/handles/${h.id}/remove`, { method: 'POST' }))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void run(async () => {
            await call(`/people/${record.id}/handles`, { method: 'POST', json: { provider, handle } })
            setHandle('')
          })
        }}
      >
        <div>
          <Label htmlFor="acct-provider">Channel</Label>
          <select
            id="acct-provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="mt-1 h-9 rounded-md border border-line bg-white px-2 text-sm"
          >
            <option value="telegram">Telegram</option>
            <option value="slack">Slack</option>
          </select>
        </div>
        <div className="min-w-40 flex-1">
          <Label htmlFor="acct-handle">{provider === 'telegram' ? 'Username or id' : 'Member id'}</Label>
          <Input
            id="acct-handle"
            value={handle}
            maxLength={200}
            placeholder={provider === 'telegram' ? '@meera_i' : 'U01ABCDEF'}
            onChange={(e) => setHandle(e.target.value)}
            className="mt-1"
          />
        </div>
        <Button size="sm" type="submit" disabled={!handle.trim()}>
          List account
        </Button>
      </form>
      <div className="mt-4 border-t border-line pt-3">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void run(async () => setInvite(await call(`/people/${record.id}/invite`, { method: 'POST' })))}
        >
          Make an invite link
        </Button>
        {invite && !invite.available && (
          <p role="status" className="mt-2 text-sm text-body">
            Connect Telegram first: <Link href="/dashboard/connections" className="underline">Connections</Link>.
          </p>
        )}
        {invite?.available && (
          <p role="status" className="mt-2 text-sm text-body [overflow-wrap:anywhere]">
            Send this to {record.name} (works once, {invite.expires_days} days): <span className="font-mono">{invite.url}</span>
          </p>
        )}
      </div>
      <ActionError action="save that" error={error} />
    </section>
  )
}
