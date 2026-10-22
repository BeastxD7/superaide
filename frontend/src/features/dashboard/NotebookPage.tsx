'use client'

import { Search } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useMemo, useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { CATEGORY_LABEL, SOURCE_LABEL, useAppData } from '@/features/dashboard/data'
import { call, type Memory } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { formatDay } from '@/lib/dates'
import { useSession } from '@/lib/session-context'

export function NotebookPage() {
  const { me } = useSession()
  const { data, error, reload } = useAppData<{ memories: Memory[] }>('/notebook')
  const [filter, setFilter] = useState<string>('all')
  const [query, setQuery] = useState('')
  const [confirm, setConfirm] = useState<string | null>(null)
  const [forgetting, setForgetting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const name = me?.agent.name ?? 'Ava'

  const memories = data?.memories
  const categories = useMemo(() => [...new Set((memories ?? []).map((m) => m.category))], [memories])
  // The kind filter can outlive its last note (forgotten, or gone after a reload): fall back to Everything.
  const active = categories.includes(filter) ? filter : 'all'
  const shown = (memories ?? []).filter(
    (m) => (active === 'all' || m.category === active) && m.text.toLowerCase().includes(query.trim().toLowerCase()),
  )

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

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">{name}&apos;s notebook</h1>
        <p className="mt-1 text-body">Everything {name} remembers about you, and where she learned it. Only you can see it.</p>

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
