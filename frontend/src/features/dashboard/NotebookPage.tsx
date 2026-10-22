'use client'

import { Network, Search } from 'lucide-react'
import Link from 'next/link'
import { AnimatePresence, motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { CATEGORY_LABEL, SOURCE_LABEL, useAppData } from '@/features/dashboard/data'
import { call, retryDocument, type Conflict, type ConflictCard, type Memory, type SharedDoc } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { formatDay } from '@/lib/dates'
import { useSession } from '@/lib/session-context'

export function NotebookPage() {
  const { me } = useSession()
  const { data, error, reload } = useAppData<{ memories: Memory[] }>('/notebook')
  const conflicts = useAppData<{ conflicts: Conflict[] }>('/notebook/conflicts')
  const files = useAppData<SharedDoc[]>('/intro/documents')
  const failed = (files.data ?? []).filter((d) => d.status === 'failed')
  const [retrying, setRetrying] = useState<string | null>(null)
  const [filter, setFilter] = useState<string>('all')
  const [where, setWhere] = useState<string>('all')
  const [query, setQuery] = useState('')
  const [confirm, setConfirm] = useState<string | null>(null)
  const [forgetting, setForgetting] = useState(false)
  const [passages, setPassages] = useState<Record<string, string>>({})
  const [actionError, setActionError] = useState<string | null>(null)
  const name = me?.agent.name ?? 'Ava'

  const memories = data?.memories
  const categories = useMemo(() => [...new Set((memories ?? []).map((m) => m.category))], [memories])
  // The kind filter can outlive its last note (forgotten, or gone after a reload): fall back to Everything.
  const active = categories.includes(filter) ? filter : 'all'
  // Where it was learned: a shared file or link by name, otherwise the channel or step.
  const whereOf = (m: Memory) => m.document ?? (SOURCE_LABEL[m.source] ?? 'from chat').replace(/^from /, '')
  const origins = useMemo(() => [...new Set((memories ?? []).map(whereOf))], [memories])
  const activeWhere = origins.includes(where) ? where : 'all'
  const shown = (memories ?? []).filter(
    (m) =>
      (active === 'all' || m.category === active) &&
      (activeWhere === 'all' || whereOf(m) === activeWhere) &&
      m.text.toLowerCase().includes(query.trim().toLowerCase()),
  )

  async function showPassage(id: string) {
    setActionError(null)
    try {
      const p = await call<{ text: string }>(`/passages/${id}`)
      setPassages((cur) => ({ ...cur, [id]: p.text }))
    } catch (e) {
      setActionError(errorMessage(e))
    }
  }

  async function forget(id: string) {
    if (forgetting) return
    setActionError(null)
    setForgetting(true)
    try {
      await call(`/notebook/${id}/forget`, { method: 'POST' })
      setConfirm(null)
      await reload()
    } catch (e) {
      setActionError(errorMessage(e))
    } finally {
      setForgetting(false)
    }
  }

  async function decide(id: string, choice: 'keep' | 'new') {
    setActionError(null)
    try {
      await call(`/notebook/conflicts/${id}/resolve`, { method: 'POST', json: { choice } })
      await Promise.all([conflicts.reload(), reload()])
    } catch (e) {
      setActionError(errorMessage(e))
    }
  }

  const origin = (c: ConflictCard) => c.document ?? (SOURCE_LABEL[c.source] ?? 'from chat').replace(/^from /, '')

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">{name}&apos;s notebook</h1>
        <p className="mt-1 text-body">Everything {name} remembers about you, and where she learned it. Only you can see it.</p>

        {(conflicts.data?.conflicts.length ?? 0) > 0 && (
          <section aria-label="Which is current?" className="mt-6 space-y-2">
            <h2 className="text-lg font-medium text-ink">Which is current?</h2>
            <p className="text-sm text-body">
              These came from a source {name} trusts less than you. {name} isn&apos;t using them until you decide.
            </p>
            {conflicts.data?.conflicts.map(({ claim, current }) => (
              <div key={claim.id} className="rounded-xl border border-violet-200 bg-violet-50/40 p-4">
                {current && (
                  <p className="text-sm text-ink">
                    <span className="text-xs text-muted">Now: from {origin(current)}</span>
                    <br />
                    {current.text}
                  </p>
                )}
                <p className="mt-2 text-sm text-ink">
                  <span className="text-xs text-muted">{current ? 'Another source says' : 'Held for your review'}: from {origin(claim)}</span>
                  <br />
                  {claim.text}
                </p>
                <div className="mt-3 flex gap-2">
                  {current && (
                    <Button size="sm" variant="outline" onClick={() => void decide(claim.id, 'keep')}>
                      Keep what I have
                    </Button>
                  )}
                  <Button size="sm" onClick={() => void decide(claim.id, 'new')}>
                    {current ? 'Use the new one' : 'Use this'}
                  </Button>
                  {!current && (
                    <Button size="sm" variant="ghost" onClick={() => void decide(claim.id, 'keep')}>
                      Discard
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </section>
        )}


        <Link
          href="/dashboard/notebook/brain"
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium text-ink hover:bg-surface"
        >
          <Network className="size-4 text-violet-700" aria-hidden /> Glass brain: see it as a map
        </Link>

        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative sm:w-72">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the notebook" aria-label="Search the notebook" className="pl-9" />
          </div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by kind">
            {['all', ...categories].map((c) => (
              <Button
                key={c}
                size="sm"
                variant={active === c ? 'default' : 'outline'}
                aria-pressed={active === c}
                onClick={() => setFilter(c)}
              >
                {c === 'all' ? 'Everything' : (CATEGORY_LABEL[c] ?? 'Note')}
              </Button>
            ))}
          </div>
        </div>

        {origins.length > 1 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by where it was learned">
            <span className="text-xs text-muted">Learned from</span>
            {['all', ...origins].map((o) => (
              <Button key={o} size="sm" variant={activeWhere === o ? 'default' : 'outline'} aria-pressed={activeWhere === o} onClick={() => setWhere(o)}>
                {o === 'all' ? 'Anywhere' : o}
              </Button>
            ))}
          </div>
        )}

        {failed.length > 0 && (
          <ul className="mt-4 space-y-2" aria-label="Files that could not be read">
            {failed.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 text-sm">
                <span className="min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">I couldn&apos;t read {d.title}.</span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={retrying !== null}
                  onClick={async () => {
                    setRetrying(d.id)
                    setActionError(null)
                    try {
                      await retryDocument(d.id)
                      await Promise.all([files.reload(), reload()])
                    } catch (e) {
                      setActionError(errorMessage(e))
                    } finally {
                      setRetrying(null)
                    }
                  }}
                >
                  {retrying === d.id ? 'Reading…' : 'Retry'}
                </Button>
              </li>
            ))}
          </ul>
        )}

        {(error || actionError) && (
          <div className="mt-4">
            {error && !memories ? (
              <LoadError what="the notebook" error={error} onRetry={reload} />
            ) : error ? (
              <ActionError action="refresh the notebook" error={error} />
            ) : (
              <ActionError action="forget that" error={actionError} />
            )}
          </div>
        )}

        <ul className="mt-6 space-y-2">
          {!memories &&
            !error &&
            [0, 1, 2].map((i) => (
              <li key={i} className="rounded-xl border border-line p-4">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="mt-3 h-4 w-3/4" />
              </li>
            ))}
          <AnimatePresence initial={false}>
            {shown.map((m) => (
              <motion.li
                key={m.id}
                layout
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: 24, transition: { duration: 0.2 } }}
                className="rounded-xl border border-line bg-white p-4"
              >
                <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                  <Badge variant="secondary" className="text-violet-700">
                    {CATEGORY_LABEL[m.category] ?? 'Note'}
                  </Badge>
                  <span>{m.document ? `from ${m.document}` : (SOURCE_LABEL[m.source] ?? 'from chat')}</span>
                  {m.shareable && <Badge variant="outline">Shareable</Badge>}
                  <time className="ml-auto" dateTime={m.at}>
                    {formatDay(m.at)}
                  </time>
                </div>
                <p className="whitespace-pre-wrap text-sm text-ink [overflow-wrap:anywhere]">{m.text}</p>
                {m.passage &&
                  (passages[m.passage] ? (
                    <blockquote className="mt-2 max-h-48 overflow-y-auto rounded-lg border-l-2 border-violet-300 bg-violet-50/50 p-3 text-xs whitespace-pre-wrap text-body">
                      {passages[m.passage]}
                    </blockquote>
                  ) : (
                    <button onClick={() => void showPassage(m.passage as string)} className="mt-2 mr-3 text-xs font-medium text-violet-700 hover:underline">
                      Show the passage
                    </button>
                  ))}
                {confirm === m.id ? (
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <span className="text-muted">{name} won&apos;t use this again.</span>
                    <Button size="sm" variant="destructive" disabled={forgetting} onClick={() => void forget(m.id)}>
                      Forget
                    </Button>
                    <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirm(null)}>
                      Keep
                    </Button>
                  </div>
                ) : (
                  <button onClick={() => setConfirm(m.id)} className="mt-2 text-xs font-medium text-muted hover:text-danger">
                    Forget this
                  </button>
                )}
              </motion.li>
            ))}
          </AnimatePresence>
          {memories && shown.length === 0 && (
            <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
              {memories.length === 0
                ? `Nothing yet. Tell ${name} "remember that…" in chat and it will appear here.`
                : query.trim()
                  ? 'Nothing matches that search.'
                  : 'No notes of that kind.'}
              {memories.length > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-3 block mx-auto"
                  onClick={() => {
                    setQuery('')
                    setFilter('all')
                    setWhere('all')
                  }}
                >
                  Show everything
                </Button>
              )}
            </li>
          )}
        </ul>
      </div>
    </div>
  )
}
