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

type Episode = {
  id: string
  channel: string
  started_at: string
  ended_at: string
  messages: number
  summary: string
  summary_by: 'extract' | 'model'
  hints: string[]
  others_present: boolean
}

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })

export function EpisodesPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<{ episodes: Episode[] }>('/episodes')
  const [confirm, setConfirm] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function erase(id: string) {
    setActionError(null)
    try {
      await call(`/episodes/${id}/erase`, { method: 'POST', json: {} })
      setConfirm(null)
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
        <h1 className="mt-2 text-3xl font-medium text-ink tight">Conversations</h1>
        <p className="mt-1 text-body">
          Each night {name} groups the day&apos;s finished conversations into short summaries so she can find them later. Only you can see
          them. Erasing one removes the summary, not the messages.
        </p>

        {error && !data && (
          <div className="mt-4">
            <LoadError what="the conversations" error={error} onRetry={reload} />
          </div>
        )}
        {!data && !error && <Skeleton className="mt-6 h-32 w-full rounded-xl" />}
        {actionError && (
          <div className="mt-4">
            <ActionError action="erase the summary" error={actionError} />
          </div>
        )}

        {data && data.episodes.length === 0 && (
          <p className="mt-6 rounded-xl border border-dashed border-line p-4 text-sm text-muted">
            Nothing yet. Summaries appear the morning after a conversation has gone quiet.
          </p>
        )}

        {data && data.episodes.length > 0 && (
          <ul className="mt-6 space-y-3" aria-label="Conversation summaries">
            {data.episodes.map((e) => (
              <li key={e.id} className="rounded-xl border border-line bg-white p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                  <span>{when(e.started_at)}</span>
                  <Badge variant="outline">{e.channel}</Badge>
                  <span>
                    {e.messages} message{e.messages === 1 ? '' : 's'}
                  </span>
                  {e.others_present && <Badge variant="outline">Others were in this chat</Badge>}
                </div>
                <p className="mt-2 text-sm text-ink [overflow-wrap:anywhere]">{e.summary}</p>
                {e.hints.length > 0 && <p className="mt-1 text-xs text-muted">{e.hints.join(', ')}</p>}
                <div className="mt-3 flex gap-2">
                  {confirm === e.id ? (
                    <>
                      <Button size="sm" variant="destructive" onClick={() => void erase(e.id)}>
                        Erase summary
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
                        Keep it
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => setConfirm(e.id)}>
                      Erase this summary
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
