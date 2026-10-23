'use client'

import { Check, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useRehearsalEnabled } from '@/features/rehearsal/useRehearsal'
import { AppError, call, type RehearsalReply, type RehearsalSession } from '@/lib/api'
import { useSession } from '@/lib/session-context'

const ROLES = ['A sceptical investor', 'An upset customer', 'A manager in a performance review', 'A tough negotiator']

const message = (e: unknown) => (e instanceof AppError ? e.message : String(e))

// Practise a hard conversation. Ava plays a generic role; nothing is sent to anyone.
export function RehearsalPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const enabled = useRehearsalEnabled()
  const [sessions, setSessions] = useState<RehearsalSession[] | null>(null)
  const [current, setCurrent] = useState<RehearsalSession | null>(null)
  const [role, setRole] = useState(ROLES[0])
  const [notes, setNotes] = useState('')
  const [line, setLine] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [paused, setPaused] = useState<string | null>(null)

  const reload = useCallback(
    () =>
      call<{ sessions: RehearsalSession[] }>('/rehearsal').then(
        (d) => setSessions(d.sessions),
        (e: unknown) => setProblem(message(e)),
      ),
    [],
  )
  useEffect(() => {
    if (enabled) void reload()
  }, [enabled, reload])

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
    } catch (e) {
      setProblem(message(e))
    } finally {
      setBusy(false)
    }
  }

  const start = () =>
    run(async () => {
      const s = await call<RehearsalSession>('/rehearsal', { method: 'POST', json: { role, notes } })
      setCurrent(s)
      setPaused(null)
      await reload()
    })
  const say = () =>
    run(async () => {
      if (!current || !line.trim()) return
      const r = await call<RehearsalReply>(`/rehearsal/${current.id}/turn`, { method: 'POST', json: { text: line } })
      setCurrent(r.session)
      setPaused(r.paused ? (r.message ?? 'Practice is paused.') : null)
      if (!r.paused) setLine('')
    })
  const end = () =>
    run(async () => {
      if (!current) return
      const r = await call<RehearsalReply>(`/rehearsal/${current.id}/end`, { method: 'POST', json: {} })
      setCurrent(r.session)
      setPaused(r.paused ? (r.message ?? 'Practice is paused.') : null)
      await reload()
    })
  const keep = () =>
    run(async () => {
      if (!current) return
      setCurrent(await call<RehearsalSession>(`/rehearsal/${current.id}/keep`, { method: 'POST', json: {} }))
      await reload()
    })
  const erase = (id: string) =>
    run(async () => {
      await call(`/rehearsal/${id}`, { method: 'DELETE' })
      if (current?.id === id) setCurrent(null)
      await reload()
    })

  if (!enabled) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
          <h1 className="text-3xl font-medium text-ink tight">Rehearsal room</h1>
          <p className="mt-2 text-body">This isn&rsquo;t turned on yet.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Rehearsal room</h1>
        <p className="mt-1 text-body">
          Practise a hard conversation before you have it. {name} plays the other side as a generic role and tells you
          afterwards what worked. It is only practice: nothing is sent to anyone, and nothing goes into {name}&rsquo;s
          memory.
        </p>

        {problem && (
          <div className="mt-4">
            <Notice tone="error">{problem}</Notice>
          </div>
        )}

        {!current && (
          <section aria-labelledby="new-rehearsal" className="mt-6 space-y-4 rounded-xl border border-line bg-white p-4">
            <h2 id="new-rehearsal" className="text-lg font-medium text-ink">
              Start a rehearsal
            </h2>
            <div>
              <label htmlFor="rehearsal-role" className="text-sm font-medium text-ink">
                Who should {name} play?
              </label>
              <Input id="rehearsal-role" className="mt-1" value={role} maxLength={120} onChange={(e) => setRole(e.target.value)} />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {ROLES.map((r) => (
                  <Button key={r} size="sm" variant={role === r ? 'default' : 'outline'} onClick={() => setRole(r)}>
                    {r}
                  </Button>
                ))}
              </div>
            </div>
            <div>
              <label htmlFor="rehearsal-notes" className="text-sm font-medium text-ink">
                What should {name} know about the situation? (optional)
              </label>
              <Textarea
                id="rehearsal-notes"
                className="mt-1"
                rows={4}
                maxLength={4000}
                value={notes}
                placeholder="Your goal, what you will not accept, what you expect them to say."
                onChange={(e) => setNotes(e.target.value)}
              />
              <p className="mt-1 text-xs text-muted">
                Only what you type here is used. {name} will not look anyone up or build a profile of a real person.
              </p>
            </div>
            <Button disabled={busy || role.trim().length < 2} onClick={() => void start()}>
              Start rehearsing
            </Button>
          </section>
        )}

        {current && (
          <section aria-label="Rehearsal" className="mt-6 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">Rehearsal, not real</Badge>
              <span className="text-sm text-body">{name} is playing: {current.role}</span>
              <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setCurrent(null)}>
                Back to all rehearsals
              </Button>
            </div>
            <ol className="space-y-2" aria-label="Transcript">
              {current.turns.length === 0 && (
                <li className="rounded-xl border border-dashed border-line p-4 text-sm text-muted">
                  You speak first. Say your opening line.
                </li>
              )}
              {current.turns.map((t, i) => (
                <li
                  key={i}
                  className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm ${t.who === 'owner' ? 'ml-auto bg-lilac-200 text-ink' : 'border border-line bg-white text-ink'}`}
                >
                  <span className="block text-xs text-muted">{t.who === 'owner' ? 'You' : `${current.role} (${name}, role-play)`}</span>
                  {t.text}
                </li>
              ))}
            </ol>

            {paused && <Notice tone="warn">{paused}</Notice>}

            {!current.ended && (
              <div className="space-y-2">
                <label htmlFor="rehearsal-line" className="sr-only">
                  Your line
                </label>
                <Textarea
                  id="rehearsal-line"
                  rows={3}
                  maxLength={2000}
                  value={line}
                  placeholder="Say what you would say."
                  onChange={(e) => setLine(e.target.value)}
                />
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy || !line.trim()} onClick={() => void say()}>
                    Say it
                  </Button>
                  <Button variant="outline" disabled={busy || current.turns.length === 0} onClick={() => void end()}>
                    End and get my debrief
                  </Button>
                </div>
              </div>
            )}

            {current.debrief && (
              <div className="rounded-xl border border-line bg-white p-4" aria-label="Debrief">
                <h2 className="text-lg font-medium text-ink">Your debrief</h2>
                {current.debrief.points.length === 0 ? (
                  <p className="mt-1 text-sm text-body">
                    {name} couldn&rsquo;t tie any advice to your own words this time, so there is no debrief. Try a longer
                    rehearsal.
                  </p>
                ) : (
                  <ol className="mt-2 space-y-3">
                    {current.debrief.points.map((p, i) => (
                      <li key={i}>
                        <p className="font-medium text-ink">{p.heading}</p>
                        <p className="text-sm text-body">{p.advice}</p>
                        <blockquote className="mt-1 border-l-2 border-lilac-200 pl-3 text-sm text-muted">
                          You said: &ldquo;{p.quote}&rdquo;
                        </blockquote>
                      </li>
                    ))}
                  </ol>
                )}
                <p className="mt-3 text-xs text-muted">
                  {current.kept
                    ? 'Kept until you erase it.'
                    : 'This rehearsal is erased after a day unless you keep it.'}
                </p>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              {!current.kept && (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => void keep()}>
                  <Check /> Keep this rehearsal
                </Button>
              )}
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void erase(current.id)}>
                <Trash2 /> Erase it now
              </Button>
            </div>
          </section>
        )}

        {!current && (
          <section aria-labelledby="past-rehearsals" className="mt-8">
            <h2 id="past-rehearsals" className="text-lg font-medium text-ink">
              Your rehearsals
            </h2>
            <ul className="mt-2 space-y-2">
              {!sessions && <Skeleton className="h-12 w-full" />}
              {sessions?.length === 0 && (
                <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
                  No rehearsals yet.
                </li>
              )}
              {sessions?.map((s) => (
                <li key={s.id} className="flex items-center gap-2 rounded-xl border border-line bg-white p-3">
                  <button className="min-w-0 flex-1 text-left" onClick={() => setCurrent(s)}>
                    <span className="block truncate font-medium text-ink">{s.role}</span>
                    <span className="block text-xs text-muted">
                      {s.turns.filter((t) => t.who === 'owner').length} lines, {s.kept ? 'kept' : 'erased after a day'}
                    </span>
                  </button>
                  <Button size="sm" variant="ghost" aria-label={`Erase rehearsal: ${s.role}`} disabled={busy} onClick={() => void erase(s.id)}>
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}
