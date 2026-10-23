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
  const { enabled, card, sent, error, reload } = usePrincipalCard()
  const [edited, setEdited] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const display = edited ?? card?.display_name ?? ''
  const setDisplay = setEdited

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
          <label htmlFor="card-display" className="text-sm font-medium text-ink">
            How people should see you
          </label>
          <Input
            id="card-display"
            value={display}
            maxLength={80}
            placeholder="A. Tan"
            onChange={(e) => setDisplay(e.target.value)}
          />
          <p className="text-xs text-muted">Anyone who opens a card sees this name, so choose it yourself. No card is sent until you set one.</p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy || !display.trim()}
              onClick={() =>
                void run(async () => {
                  await call('/principal-card', { method: 'PUT', json: { display_name: display } })
                  setDone('Saved. New replies carry the card.')
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
