'use client'

import { useMemo, useState } from 'react'
import type { Connection, Conversation, EventRow } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { TrustTier } from '@/features/console/components/TrustTier'
import { AUDIENCE_NAMES, ago } from '@/features/console/format'
import { arrayPageFetcher, pageFetcher } from '@/features/console/api'
import { usePoll } from '@/features/console/usePoll'
import { ListFooter } from '@/components/ui/list-footer'
import { SkeletonList } from '@/components/ui/skeleton'
import { usePaginated } from '@/lib/paginated'

const PAGE = 50
const THREAD_PAGE = 100

export function Conversations({ conn }: { conn: Connection }) {
  const fetcher = useMemo(() => pageFetcher<Conversation>(conn), [conn])
  const convs = usePaginated<Conversation>('/api/conversations/page', { limit: PAGE, fetcher })
  usePoll(convs, 15_000, PAGE)
  const [selected, setSelected] = useState<string | null>(null)
  const active = selected ?? convs.items[0]?.id ?? null

  if (convs.status === 'error' && convs.failed === 'first') return <p className="error">{convs.error}</p>
  if (convs.status === 'loading') return <SkeletonList count={6} rowHeight={64} what="conversations" />
  if (convs.items.length === 0)
    return (
      <div className="empty">
        <h2>No conversations yet</h2>
        <p>Message the bot on Telegram or Slack, or try the impostor simulator.</p>
      </div>
    )

  return (
    <div className="conversations">
      <nav className="conv-list" aria-label="Conversations">
        {convs.items.map((c) => (
          <button
            key={c.id}
            className={c.id === active ? 'conv active' : 'conv'}
            aria-current={c.id === active}
            onClick={() => setSelected(c.id)}
          >
            <span className="conv-title brand-label">
              {isBrand(c.provider) && <BrandIcon brand={c.provider} size={14} decorative />}
              {c.provider} {c.kind === 'dm' ? 'DM' : 'group'}
            </span>
            <span className={`audience audience-${c.audience_class}`}>{AUDIENCE_NAMES[c.audience_class] ?? c.audience_class}</span>
            <span className="quiet">
              {c.messages_more ? `${c.messages}+` : c.messages} messages, {ago(c.last_at)}
            </span>
          </button>
        ))}
        <ListFooter {...convs.footer} endLabel="All conversations" />
      </nav>
      {active && <Thread key={active} conn={conn} contextId={active} />}
    </div>
  )
}

function Thread({ conn, contextId }: { conn: Connection; contextId: string }) {
  const fetcher = useMemo(() => arrayPageFetcher<EventRow>(conn), [conn])
  const params = useMemo(() => ({ context_id: contextId }), [contextId])
  const list = usePaginated<EventRow>('/api/events', { limit: THREAD_PAGE, params, fetcher })
  usePoll(list, 5_000, THREAD_PAGE)
  if (list.status === 'error' && list.failed === 'first') return <p className="error">{list.error}</p>
  if (list.status === 'loading') return <SkeletonList count={5} rowHeight={80} what="messages" />
  const rows = [...list.items].reverse() // newest first from the API; a thread reads oldest first
  return (
    <div>
      <ListFooter {...list.footer} endLabel="Start of the conversation" onLoadMore={list.footer.onLoadMore} />
      <ol className="thread">
        {rows.map((e) => (
          <li key={e.id} className={e.direction === 'out' ? 'msg out' : 'msg in'}>
            <div className="msg-meta">
              <strong>{e.sender}</strong>
              {e.direction === 'in' && <TrustTier level={e.trust} />}
              <span className="quiet">{ago(e.at)}</span>
            </div>
            <p>{e.content}</p>
            {e.direction === 'in' && (
              <p className="msg-audience">
                {e.audience_has_unknown
                  ? 'Audience includes people Ava cannot verify, so only public memories were usable.'
                  : `Audience: ${e.audience_size} verified ${e.audience_size === 1 ? 'person' : 'people'}.`}
              </p>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
