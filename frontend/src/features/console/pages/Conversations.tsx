'use client'

import { useState } from 'react'
import type { Connection, Conversation, EventRow } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { TrustTier } from '@/features/console/components/TrustTier'
import { AUDIENCE_NAMES, ago } from '@/features/console/format'
import { useApi } from '@/features/console/useApi'

export function Conversations({ conn }: { conn: Connection }) {
  const convs = useApi<Conversation[]>(conn, '/api/conversations')
  const [selected, setSelected] = useState<string | null>(null)
  const active = selected ?? convs.data?.[0]?.id ?? null

  if (convs.error) return <p className="error">{convs.error.message}</p>
  if (!convs.data) return <p className="quiet">Loading conversations…</p>
  if (convs.data.length === 0)
    return (
      <div className="empty">
        <h2>No conversations yet</h2>
        <p>Message the bot on Telegram or Slack, or try the impostor simulator.</p>
      </div>
    )

  return (
    <div className="conversations">
      <nav className="conv-list" aria-label="Conversations">
        {convs.data.map((c) => (
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
              {c.messages} messages, {ago(c.last_at)}
            </span>
          </button>
        ))}
      </nav>
      {active && <Thread conn={conn} contextId={active} />}
    </div>
  )
}

function Thread({ conn, contextId }: { conn: Connection; contextId: string }) {
  const { data, error } = useApi<EventRow[]>(conn, `/api/events?context_id=${contextId}&limit=200`, 3000)
  if (error) return <p className="error">{error.message}</p>
  if (!data) return <p className="quiet">Loading messages…</p>
  const rows = [...data].reverse()
  return (
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
  )
}
