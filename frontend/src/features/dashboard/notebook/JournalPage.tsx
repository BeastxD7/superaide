'use client'

import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { useSession } from '@/lib/session-context'

type Prediction = {
  id: string
  statement: string
  quote: string
  reason: string
  topic: string
  confidence_pct: number
  inferred: boolean
  due_on: string | null
  due: boolean
  source: 'chat' | 'manual'
  status: 'open' | 'resolved'
  came_true: boolean | null
  note: string
}
type Calibration = {
  resolved: number
  right: number
  hit_rate: number
  brier: number
  buckets: { from: number; to: number; count: number; stated: number; hit_rate: number }[]
  coaching: string[]
}
type Journal = { enabled: boolean; predictions: Prediction[]; calibration: Calibration | null; resolved: number; needed: number }

const TOPICS: Record<string, string> = { timeline: 'Timeline', people: 'People', money: 'Money', work: 'Work', other: 'Other' }
const pct = (n: number) => `${Math.round(n * 100)}%`

export function JournalPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<Journal>('/journal')
  const [statement, setStatement] = useState('')
  const [sure, setSure] = useState('80')
  const [due, setDue] = useState('')
  const [topic, setTopic] = useState('timeline')
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmOff, setConfirmOff] = useState(false)

  async function act(path: string, json?: unknown) {
    setActionError(null)
    try {
      await call(path, { method: 'POST', json })
      await reload()
      return true
    } catch (e) {
      setActionError(errorMessage(e))
      return false
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault()
    const ok = await act('/journal/predictions', {
      statement,
      confidence_pct: Number(sure),
      due_on: due || null,
      topic,
    })
    if (ok) {
      setStatement('')
      setDue('')
    }
  }

  const open = data?.predictions.filter((p) => p.status === 'open') ?? []
  const done = data?.predictions.filter((p) => p.status === 'resolved') ?? []

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <Link href="/dashboard/notebook" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Notebook
        </Link>
        <h1 className="mt-2 text-3xl font-medium text-ink tight">Decision journal</h1>
        <p className="mt-1 text-body">
          {name} keeps score of what you expect to happen, checks it later, and shows how often you&apos;re right. Only you can see it.
        </p>

        {error && !data && (
          <div className="mt-4">
            <LoadError what="the journal" error={error} onRetry={reload} />
          </div>
        )}
        {!data && !error && <Skeleton className="mt-6 h-40 w-full rounded-xl" />}
        {actionError && (
          <div className="mt-4">
            <ActionError action="update the journal" error={actionError} />
          </div>
        )}

        {data && !data.enabled && (
          <section className="mt-6 rounded-xl border border-line bg-white p-5" aria-labelledby="journal-off">
            <h2 id="journal-off" className="text-base font-medium text-ink">
              Off for now
            </h2>
            <p className="mt-1 text-sm text-body">
              When on, {name} notes predictions you make in chat, in your own words (&quot;I&apos;ll ship by 5 Nov, I&apos;m 90% sure&quot;), and
              you can add them here too. Once ten are resolved you&apos;ll see how well your confidence matches what happened. Nothing from
              other people is ever scored as yours. Turning it off deletes the whole journal.
            </p>
            <Button className="mt-4" onClick={() => void act('/journal/settings', { enabled: true })}>
              Turn on the journal
            </Button>
          </section>
        )}

        {data?.enabled && (
          <>
            <form onSubmit={(e) => void add(e)} className="mt-6 rounded-xl border border-line bg-white p-4" aria-label="Add a prediction">
              <label htmlFor="pred" className="text-sm font-medium text-ink">
                What do you expect to happen?
              </label>
              <Input id="pred" value={statement} onChange={(e) => setStatement(e.target.value)} maxLength={500} placeholder="The beta ships by 5 November" className="mt-1" />
              <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
                <label className="flex flex-col gap-1">
                  <span className="text-muted">How sure</span>
                  <select value={sure} onChange={(e) => setSure(e.target.value)} className="h-9 rounded-md border border-line bg-white px-2">
                    {[50, 60, 70, 80, 90, 95, 99].map((n) => (
                      <option key={n} value={n}>
                        {n}%
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-muted">Kind</span>
                  <select value={topic} onChange={(e) => setTopic(e.target.value)} className="h-9 rounded-md border border-line bg-white px-2">
                    {Object.entries(TOPICS).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-muted">Known by</span>
                  <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} className="w-40" />
                </label>
                <Button type="submit" disabled={statement.trim().length < 3}>
                  Add to journal
                </Button>
              </div>
            </form>

            <section className="mt-6" aria-labelledby="open-h">
              <h2 id="open-h" className="text-base font-medium text-ink">
                Waiting to be checked
              </h2>
              <ul className="mt-2 space-y-2">
                {open.length === 0 && <li className="rounded-xl border border-dashed border-line p-4 text-sm text-muted">Nothing open.</li>}
                {open.map((p) => (
                  <li key={p.id} className="rounded-xl border border-line bg-white p-4">
                    <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <Badge variant="secondary" className="text-violet-700">
                        {TOPICS[p.topic] ?? 'Other'}
                      </Badge>
                      <span>
                        {p.confidence_pct}% sure{p.inferred ? ' (from your wording)' : ''}
                      </span>
                      {p.due_on && <span>· by {new Date(p.due_on + 'T00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</span>}
                      {p.due && <Badge variant="outline">Due: how did it turn out?</Badge>}
                    </div>
                    <p className="text-sm text-ink [overflow-wrap:anywhere]">{p.statement}</p>
                    {p.source === 'chat' && p.quote && <p className="mt-1 text-xs text-muted">You said: &quot;{p.quote}&quot;</p>}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => void act(`/journal/predictions/${p.id}/resolve`, { came_true: true })}>
                        It came true
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => void act(`/journal/predictions/${p.id}/resolve`, { came_true: false })}>
                        It didn&apos;t
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void act(`/journal/predictions/${p.id}/erase`)}>
                        Erase
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section className="mt-8" aria-labelledby="cal-h">
              <h2 id="cal-h" className="text-base font-medium text-ink">
                Your calibration
              </h2>
              {!data.calibration ? (
                <p className="mt-2 rounded-xl border border-dashed border-line p-4 text-sm text-muted">
                  {data.resolved} of {data.needed} resolved. After {data.needed}, you&apos;ll see how often you&apos;re right at each confidence.
                </p>
              ) : (
                <div className="mt-2 rounded-xl border border-line bg-white p-4 text-sm">
                  <p className="text-ink">
                    Right {data.calibration.right} of {data.calibration.resolved} times ({pct(data.calibration.hit_rate)}). Brier score{' '}
                    {data.calibration.brier} (lower is better; 0.25 is a coin flip).
                  </p>
                  <ul className="mt-3 space-y-2" aria-label="Confidence against results">
                    {data.calibration.buckets.map((b) => (
                      <li key={b.from} className="grid grid-cols-[88px_1fr] items-center gap-2 text-xs">
                        <span className="text-muted">
                          {b.from}s ({b.count})
                        </span>
                        <span className="space-y-1">
                          <span className="flex items-center gap-2">
                            <span className="h-2 rounded bg-violet-300" style={{ width: pct(b.stated) }} />
                            <span>said {pct(b.stated)}</span>
                          </span>
                          <span className="flex items-center gap-2">
                            <span className="h-2 rounded bg-violet-700" style={{ width: pct(b.hit_rate) }} />
                            <span>right {pct(b.hit_rate)}</span>
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                  {data.calibration.coaching.map((c) => (
                    <p key={c} className="mt-3 rounded-lg bg-surface p-3 text-ink">
                      {c}
                    </p>
                  ))}
                </div>
              )}
            </section>

            {done.length > 0 && (
              <section className="mt-8" aria-labelledby="done-h">
                <h2 id="done-h" className="text-base font-medium text-ink">
                  Checked
                </h2>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {done.map((p) => (
                    <li key={p.id} className="flex flex-wrap items-center gap-2 text-body">
                      <Badge variant={p.came_true ? 'secondary' : 'outline'}>{p.came_true ? 'Came true' : 'Didn’t'}</Badge>
                      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{p.statement}</span>
                      <span className="text-xs text-muted">{p.confidence_pct}% sure</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <div className="mt-10 border-t border-line pt-4 text-sm">
              {confirmOff ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-muted">This deletes every prediction in your journal.</span>
                  <Button size="sm" variant="destructive" onClick={() => void act('/journal/settings', { enabled: false }).then(() => setConfirmOff(false))}>
                    Delete the journal
                  </Button>
                  <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirmOff(false)}>
                    Keep it
                  </Button>
                </div>
              ) : (
                <button onClick={() => setConfirmOff(true)} className="font-medium text-muted hover:text-danger">
                  Turn off and delete the journal
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
