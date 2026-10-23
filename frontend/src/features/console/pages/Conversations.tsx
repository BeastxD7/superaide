'use client'

import { useState } from 'react'
import clsx from 'clsx'
import type { Connection, Conversation, EventRow } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { TrustTier } from '@/features/console/components/TrustTier'
import { AUDIENCE_NAMES, ago } from '@/features/console/format'
import { Badge, Card, EmptyState, LoadingBlock, Notice } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

export function Conversations({ conn }: { conn: Connection }) {
  const convs = useApi<Conversation[]>(conn, '/api/conversations')
  const [selected, setSelected] = useState<string | null>(null)
  const active = selected ?? convs.data?.[0]?.id ?? null

  if (convs.error) return <Notice tone="danger">{convs.error.message}</Notice>
  if (!convs.data) return <LoadingBlock label="Loading conversations…" />
  if (convs.data.length === 0) return <EmptyState title="No conversations yet">Message the bot on Telegram or Slack, or try the impostor simulator.</EmptyState>

  return (
    <div className="c-split">
      <Card className="c-split-side">
        <nav className="conv-list" aria-label="Conversations">
          {convs.data.map((c) => (
            <button key={c.id} className={clsx('conv', c.id === active && 'active')} aria-current={c.id === active} onClick={() => setSelected(c.id)}>
              <span className="conv-title brand-label">
                {isBrand(c.provider) && <BrandIcon brand={c.provider} size={14} decorative />}
                {c.provider} {c.kind === 'dm' ? 'DM' : 'group'}
              </span>
              <span className={`audience audience-${c.audience_class}`}>{AUDIENCE_NAMES[c.audience_class] ?? c.audience_class}</span>
              <span className="quiet">
                {c.messages} messages, {ago(c.last_at)}
              </span>
            </button>
          ))}
        </nav>
      </Card>
      <Card className="c-split-main">{active && <Thread key={active} conn={conn} contextId={active} />}</Card>
    </div>
  )
}

function Thread({ conn, contextId }: { conn: Connection; contextId: string }) {
  const { data, error } = useApi<EventRow[]>(conn, `/api/events?context_id=${contextId}&limit=200`, 3000)
  if (error) return <Notice tone="danger">{error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading messages…" />
  const rows = [...data].reverse()
  return (
    <ol className="thread" aria-label="Messages" tabIndex={0}>
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
              {e.audience_has_unknown ? (
                <Badge tone="warn" dot>
                  Audience includes people Ava cannot verify, so only public memories were usable.
                </Badge>
              ) : (
                `Audience: ${e.audience_size} verified ${e.audience_size === 1 ? 'person' : 'people'}.`
              )}
            </p>
          )}
        </li>
      ))}
    </ol>
  )
}
