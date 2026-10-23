'use client'

import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { useSession } from '@/lib/session-context'

type Receipt = { type: 'reminder' | 'count'; id?: string; label: string; at?: string; due?: string; status?: string; count?: number }
type Finding = { kind: string; pattern_key: string; text: string; href: string; receipts: Receipt[]; feedback: 'not_useful' | 'mute' | null }
type Note = { id: string; week_start: string; findings: Finding[] }
type Mirror = { enabled: boolean; note: Note | null; earlier: Note[]; muted: number }

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })

function FindingCard({ f, onFeedback }: { f: Finding; onFeedback: (action: 'not_useful' | 'mute') => void }) {
  return (
    <li className="rounded-xl border border-line bg-white p-4">
      <p className="text-sm text-ink [overflow-wrap:anywhere]">{f.text}</p>
      <details className="mt-2 text-xs text-muted">
        <summary className="cursor-pointer font-medium text-violet-700">Show the receipts ({f.receipts.length})</summary>
        <ul className="mt-2 space-y-1">
          {f.receipts.map((r, i) => (
            <li key={r.id ?? `${r.label}-${i}`} className="[overflow-wrap:anywhere]">
              {r.type === 'reminder'
                ? `“${r.label}”, set ${day(r.at ?? '')}, due ${day(r.due ?? '')} (${r.status})`
                : `${r.label}: ${r.count} messages`}
            </li>
          ))}
        </ul>
        <Link href={f.href} className="mt-2 inline-block font-medium text-violet-700 underline">
          Open it
        </Link>
      </details>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {f.feedback ? (
          <Badge variant="outline">{f.feedback === 'mute' ? 'Won’t be mentioned again' : 'Marked not useful'}</Badge>
        ) : (
          <>
            <Button size="sm" variant="outline" onClick={() => onFeedback('not_useful')}>
              Not useful
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onFeedback('mute')}>
              Don&apos;t mention this again
            </Button>
          </>
        )}
      </div>
    </li>
  )
}

export function MirrorPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<Mirror>('/mirror')
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirmOff, setConfirmOff] = useState(false)

  async function act(path: string, json: unknown) {
    setActionError(null)
    try {
      await call(path, { method: 'POST', json })
      await reload()
    } catch (e) {
      setActionError(errorMessage(e))
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <Link href="/dashboard/notebook" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Notebook
        </Link>
        <h1 className="mt-2 text-3xl font-medium text-ink tight">The Mirror</h1>
        <p className="mt-1 text-body">
          Once a week, up to three things {name} noticed in your own reminders and messages, each with the rows it was counted from. Only you can see it.
        </p>

        {error && !data && (
          <div className="mt-4">
            <LoadError what="the Mirror" error={error} onRetry={reload} />
          </div>
        )}
        {!data && !error && <Skeleton className="mt-6 h-32 w-full rounded-xl" />}
        {actionError && (
          <div className="mt-4">
            <ActionError action="update the Mirror" error={actionError} />
          </div>
        )}

        {data && !data.enabled && (
          <section className="mt-6 rounded-xl border border-line bg-white p-5" aria-labelledby="mirror-off">
            <h2 id="mirror-off" className="text-base font-medium text-ink">
              Off for now
            </h2>
            <p className="mt-1 text-sm text-body">
              It counts things like a reminder you keep setting again, reminders that went off and are still open, and a big change in how
              much you message. It never guesses at mood or health, and every line has proof you can open. No AI is used. Turning it off
              deletes the notes.
            </p>
            <Button className="mt-4" onClick={() => void act('/mirror/settings', { enabled: true })}>
              Turn on the Mirror
            </Button>
          </section>
        )}

        {data?.enabled && (
          <>
            <section className="mt-6" aria-labelledby="week-h">
              <h2 id="week-h" className="text-base font-medium text-ink">
                This week
              </h2>
              {data.note ? (
                <ul className="mt-2 space-y-2">
                  {data.note.findings.map((f, i) => (
                    <FindingCard key={f.pattern_key} f={f} onFeedback={(a) => void act(`/mirror/notes/${data.note?.id}/findings/${i}/feedback`, { action: a })} />
                  ))}
                </ul>
              ) : (
                <p className="mt-2 rounded-xl border border-dashed border-line p-4 text-sm text-muted">
                  Nothing worth pointing out this week. {name} only says something when the numbers back it up.
                </p>
              )}
            </section>

            {data.earlier.length > 0 && (
              <section className="mt-8" aria-labelledby="earlier-h">
                <h2 id="earlier-h" className="text-base font-medium text-ink">
                  Earlier weeks
                </h2>
                {data.earlier.map((n) => (
                  <div key={n.id} className="mt-3">
                    <p className="text-xs text-muted">Week of {day(n.week_start)}</p>
                    <ul className="mt-1 list-disc pl-5 text-sm text-body">
                      {n.findings.map((f) => (
                        <li key={f.pattern_key}>{f.text}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </section>
            )}

            <div className="mt-10 border-t border-line pt-4 text-sm">
              {confirmOff ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-muted">This deletes every note.</span>
                  <Button size="sm" variant="destructive" onClick={() => void act('/mirror/settings', { enabled: false }).then(() => setConfirmOff(false))}>
                    Turn off and delete
                  </Button>
                  <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirmOff(false)}>
                    Keep it
                  </Button>
                </div>
              ) : (
                <button onClick={() => setConfirmOff(true)} className="font-medium text-muted hover:text-danger">
                  Turn off the Mirror
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
