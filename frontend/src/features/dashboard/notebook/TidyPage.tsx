'use client'

import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { SOURCE_LABEL, useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { useSession } from '@/lib/session-context'

type Change = {
  id: string
  at: string
  kind: 'duplicate' | 'near_duplicate' | 'contradiction'
  reason: string
  decided_by: 'code' | 'model'
  source: string
  retired: { id: string; content: string }
  kept: { id: string; content: string } | null
  undone_at: string | null
  can_undo: boolean
}
type Page = { items: Change[]; next_cursor: string | null; has_more: boolean }

const KIND: Record<Change['kind'], string> = {
  duplicate: 'Same note twice',
  near_duplicate: 'Same meaning',
  contradiction: 'Replaced by a newer note',
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })

export function TidyPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<Page>('/tidy')
  const [more, setMore] = useState<Page | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const items = [...(data?.items ?? []), ...(more?.items ?? [])]
  const hasMore = more ? more.has_more : (data?.has_more ?? false)
  const cursor = more ? more.next_cursor : (data?.next_cursor ?? null)

  async function undo(id: string) {
    setActionError(null)
    setBusy(id)
    try {
      await call(`/tidy/${id}/undo`, { method: 'POST', json: {} })
      setMore(null)
      await reload()
    } catch (e) {
      setActionError(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function older() {
    if (!cursor) return
    setActionError(null)
    try {
      const next = await call<Page>(`/tidy?cursor=${encodeURIComponent(cursor)}`)
      setMore({ items: [...(more?.items ?? []), ...next.items], next_cursor: next.next_cursor, has_more: next.has_more })
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
        <h1 className="mt-2 text-3xl font-medium text-ink tight">What {name} tidied last night</h1>
        <p className="mt-1 text-body">
          Each night {name} merges notes that say the same thing and retires one that a newer note replaces. Nothing is deleted: the
          note stays on file and you can bring it back with Undo. Only you can see this.
        </p>

        {error && !data && (
          <div className="mt-4">
            <LoadError what="the tidy log" error={error} onRetry={reload} />
          </div>
        )}
        {!data && !error && <Skeleton className="mt-6 h-32 w-full rounded-xl" />}
        {actionError && (
          <div className="mt-4">
            <ActionError action="undo that" error={actionError} />
          </div>
        )}

        {data && items.length === 0 && (
          <p className="mt-6 rounded-xl border border-dashed border-line p-4 text-sm text-muted">
            Nothing tidied yet. When {name} merges or retires a note overnight, it shows up here.
          </p>
        )}

        {items.length > 0 && (
          <ul className="mt-6 space-y-3" aria-label="Tidied notes">
            {items.map((c) => (
              <li key={c.id} className="rounded-xl border border-line bg-white p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{when(c.at)}</span>
                  <Badge variant="outline">{KIND[c.kind]}</Badge>
                  <span>{SOURCE_LABEL[c.source] ?? c.source}</span>
                  {c.undone_at && <Badge variant="outline">Undone {when(c.undone_at)}</Badge>}
                </div>
                <p className="mt-2 text-xs font-medium uppercase tracking-wide text-muted">Retired</p>
                <p className="text-sm text-ink [overflow-wrap:anywhere]">{c.retired.content}</p>
                {c.kept && (
                  <>
                    <p className="mt-2 text-xs font-medium uppercase tracking-wide text-muted">Kept</p>
                    <p className="text-sm text-ink [overflow-wrap:anywhere]">{c.kept.content}</p>
                  </>
                )}
                <p className="mt-2 text-xs text-muted">{c.reason}</p>
                {!c.undone_at && (
                  <div className="mt-3">
                    {c.can_undo ? (
                      <Button size="sm" variant="outline" disabled={busy === c.id} onClick={() => void undo(c.id)}>
                        Undo
                      </Button>
                    ) : (
                      <p className="text-xs text-muted">This note has changed since, so it was left as it is.</p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {hasMore && (
          <Button className="mt-4" variant="outline" onClick={() => void older()}>
            Show older
          </Button>
        )}
      </div>
    </div>
  )
}
