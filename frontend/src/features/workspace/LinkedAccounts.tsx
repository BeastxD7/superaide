'use client'

import { ExternalLink, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AppError, call } from '@/lib/api'
import { Notice } from '@/components/site'

// The chat accounts linked to people, and which bot or app each one was confirmed on (backend
// app/linked.py). A linked account that writes through a new bot gets a fixed reply until it's
// confirmed here; Unlink takes effect on its next message.

export type LinkedAccount = {
  id: string
  provider: 'telegram' | 'slack'
  mine: boolean
  person: string
  account: string
  username: string | null
  endpoint: string
  linked_at: string | null
  last_seen_at: string | null
  last_seen_unconfirmed_at: string | null
  pending: { reason: 'new_endpoint' | 'stale'; endpoint: string; since: string | null; can_confirm: boolean } | null
}

function when(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? 'unknown' : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function LinkedAccounts({
  provider,
  accounts,
  onChanged,
}: {
  provider: 'telegram' | 'slack'
  accounts: LinkedAccount[]
  onChanged: () => Promise<void>
}) {
  const rows = accounts.filter((a) => a.provider === provider)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [links, setLinks] = useState<Record<string, string>>({})
  const what = provider === 'telegram' ? 'bot' : 'app'
  const waitingOnLink = rows.some((a) => a.pending && links[a.id])

  // After the confirm link is opened, watch for it to be used (as linking Telegram does).
  useEffect(() => {
    if (!waitingOnLink) return
    const t = window.setInterval(() => void onChanged(), 3000)
    return () => window.clearInterval(t)
  }, [waitingOnLink, onChanged])

  if (!rows.length) return null

  async function confirm(a: LinkedAccount) {
    setBusy(a.id)
    setError(null)
    try {
      const r = await call<{ url?: string; confirmed?: boolean }>(`/channels/linked/${a.id}/confirm`, { method: 'POST' })
      if (r.url) {
        setLinks((l) => ({ ...l, [a.id]: r.url as string }))
        window.open(r.url, '_blank', 'noopener,noreferrer')
      } else {
        await onChanged()
      }
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function unlink(a: LinkedAccount) {
    setBusy(a.id)
    setError(null)
    try {
      await call(`/channels/linked/${a.id}/unlink`, { method: 'POST' })
      await onChanged()
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mb-3 space-y-2">
      <p className="text-xs font-medium text-ink">Linked accounts</p>
      {rows.map((a) => (
        <div key={a.id} className="rounded-xl bg-mist px-3 py-2.5 text-sm">
          {a.pending && (
            <div className="mb-2 flex gap-2 rounded-lg bg-amber-50 px-2.5 py-2 text-ink">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-amber-600" />
              <div className="min-w-0 flex-1">
                <p>
                  {a.pending.reason === 'stale'
                    ? `${a.mine ? 'Your' : `${a.person}'s`} account wrote after a long silence.`
                    : `${a.mine ? 'Your' : `${a.person}'s`} account wrote to a new ${what} (${a.pending.endpoint}).`}{' '}
                  {a.last_seen_unconfirmed_at ? `Last message there ${when(a.last_seen_unconfirmed_at)}. ` : ''}Until it's confirmed, I treat it as someone I don't know. If it wasn't {a.mine ? 'you' : 'them'}, unlink it.
                </p>
                {links[a.id] ? (
                  <a
                    href={links[a.id]}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-[#1f6fa8]"
                  >
                    Open Telegram and press Start <ExternalLink className="size-3.5" />
                  </a>
                ) : a.pending.can_confirm ? (
                  <button
                    onClick={() => void confirm(a)}
                    disabled={busy === a.id}
                    className="mt-2 rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                  >
                    Confirm this {what}
                  </button>
                ) : (
                  <p className="mt-1 text-xs text-muted">Connect that {what} first, then confirm it here.</p>
                )}
              </div>
            </div>
          )}
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-ink">
                {a.mine ? 'You' : a.person} · <span className="font-mono">{a.account}</span>
                {a.username ? ` (@${a.username})` : ''}
              </p>
              <p className="text-xs text-muted">
                Confirmed on {a.endpoint} · linked {when(a.linked_at)} · last seen {when(a.last_seen_at)}
              </p>
            </div>
            <button
              onClick={() => void unlink(a)}
              disabled={busy === a.id}
              className="shrink-0 rounded-full px-2.5 py-1 text-xs text-muted hover:text-danger disabled:opacity-60"
            >
              Unlink
            </button>
          </div>
        </div>
      ))}
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  )
}
