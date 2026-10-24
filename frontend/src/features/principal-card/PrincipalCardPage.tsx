'use client'

import { useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { usePrincipalCard } from '@/features/principal-card/usePrincipalCard'
import { AppError, call } from '@/lib/api'
import { useSession } from '@/lib/session-context'

const message = (e: unknown) => (e instanceof AppError ? e.message : String(e))
const when = (unix: number) => new Date(unix * 1000).toLocaleString()

// The one-line "this is an AI acting for X" card that ends replies to other people. The owner chooses the
// name it shows, sees every card sent, and can take back one card or all of them.
export function PrincipalCardPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { enabled, card, sent, stats, error, reload } = usePrincipalCard()
  const [editedFirst, setEditedFirst] = useState<string | null>(null)
  const [editedInitial, setEditedInitial] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const first = editedFirst ?? card?.first_name ?? ''
  const initial = editedInitial ?? card?.initial ?? ''
  const shownAs = first.trim() ? (initial.trim() ? `${first.trim()} ${initial.trim().slice(0, 1).toUpperCase()}.` : first.trim()) : ''

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setProblem(message(e))
    } finally {
      setBusy(false)
    }
  }

  if (enabled === false) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
          <h1 className="text-3xl font-medium text-ink tight">Principal card</h1>
          <p className="mt-2 text-body">This isn&rsquo;t turned on yet.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Principal card</h1>
        <p className="mt-1 text-body">
          When {name} replies to someone who isn&rsquo;t you, on Gmail, Telegram or Slack, the reply ends with one honest
          line and a link: she is an AI, who she acts for, and what she may not agree to. The page shows only that.
        </p>
        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}
        {done && (
          <div className="mt-4" role="status">
            <Notice>{done}</Notice>
          </div>
        )}

        <section aria-labelledby="card-name" className="mt-6 space-y-3 rounded-xl border border-line bg-white p-4">
          <h2 id="card-name" className="text-lg font-medium text-ink">
            The name it shows
          </h2>
          <label htmlFor="card-first" className="text-sm font-medium text-ink">
            Your first name
          </label>
          <Input
            id="card-first"
            value={first}
            maxLength={40}
            placeholder="Ananya"
            onChange={(e) => setEditedFirst(e.target.value)}
          />
          <label htmlFor="card-initial" className="text-sm font-medium text-ink">
            Initial to show after it (optional)
          </label>
          <Input
            id="card-initial"
            value={initial}
            maxLength={1}
            className="w-20"
            placeholder="T"
            onChange={(e) => setEditedInitial(e.target.value)}
          />
          <p className="text-xs text-muted">
            {shownAs ? `People will see: ${shownAs}. ` : ''}Never your full name unless you type it as the first name
            yourself, which is not allowed here.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy || !first.trim()}
              onClick={() =>
                void run(async () => {
                  await call('/principal-card', { method: 'PUT', json: { first_name: first, initial } })
                  setDone('Saved.')
                })
              }
            >
              Save
            </Button>
            {card?.preview && (
              <Button variant="outline" asChild>
                <a href={card.preview} target="_blank" rel="noreferrer">
                  See how it looks
                </a>
              </Button>
            )}
          </div>
        </section>

        <section aria-labelledby="card-switch" className="mt-6 space-y-2 rounded-xl border border-line bg-white p-4">
          <h2 id="card-switch" className="text-lg font-medium text-ink">
            Add the card to my replies
          </h2>
          <p className="text-sm text-body">
            Off until you turn it on. Turning it off keeps your name and lists but adds nothing to any reply.
          </p>
          <Button
            variant={card?.opted_in ? 'outline' : 'default'}
            disabled={busy || (!card?.opted_in && !card?.first_name)}
            onClick={() =>
              void run(async () => {
                await call('/principal-card/opt-in', { method: 'POST', json: { opted_in: !card?.opted_in } })
                setDone(card?.opted_in ? 'The card is off. New replies carry nothing.' : 'The card is on. New replies carry it.')
              })
            }
          >
            {card?.opted_in ? 'Turn the card off' : 'Turn the card on'}
          </Button>
          {!card?.first_name && <p className="text-xs text-muted">Save your first name first.</p>}
        </section>

        {stats && (
          <section aria-labelledby="card-stats" className="mt-6 rounded-xl border border-line bg-white p-4">
            <h2 id="card-stats" className="text-lg font-medium text-ink">
              How it is going
            </h2>
            <dl className="mt-2 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <div><dt className="text-muted">Cards shown</dt><dd className="font-medium text-ink">{stats.shown}</dd></div>
              <div><dt className="text-muted">Cards opened</dt><dd className="font-medium text-ink">{stats.opened}</dd></div>
              <div><dt className="text-muted">Taken back</dt><dd className="font-medium text-ink">{stats.revoked_one + stats.revoked_all}</dd></div>
              <div><dt className="text-muted">Opened rate</dt><dd className="font-medium text-ink">{Math.round(stats.open_rate * 100)}%</dd></div>
            </dl>
            <p className="mt-2 text-xs text-muted">Counts only. No names and no message text are kept here.</p>
          </section>
        )}

        <section aria-labelledby="cards-sent" className="mt-8">
          <div className="flex items-center justify-between gap-2">
            <h2 id="cards-sent" className="text-lg font-medium text-ink">
              Cards sent
            </h2>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await call('/principal-card/revoke', { method: 'POST', json: {} })
                  setDone('Every card sent so far no longer opens.')
                })
              }
            >
              Revoke all cards
            </Button>
          </div>
          <ul className="mt-2 space-y-2">
            {sent.length === 0 && (
              <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
                No cards sent yet.
              </li>
            )}
            {sent.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-white p-3 text-sm">
                <span className="font-medium text-ink">{c.to || 'Someone'}</span>
                <Badge variant="secondary">{c.channel}</Badge>
                <span className="text-muted">{when(c.at)}</span>
                <Badge variant={c.status === 'active' ? 'default' : 'secondary'}>{c.status}</Badge>
                {c.status === 'active' && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    disabled={busy}
                    aria-label={`Revoke the card sent to ${c.to || 'someone'} on ${when(c.at)}`}
                    onClick={() =>
                      void run(async () => {
                        await call(`/principal-card/sent/${c.id}/revoke`, { method: 'POST', json: {} })
                        setDone('That card no longer opens.')
                      })
                    }
                  >
                    Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
