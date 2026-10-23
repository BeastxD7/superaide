'use client'

import { useState } from 'react'
import { api, type Connection, type Identity, type PersonRow } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { TrustTier } from '@/features/console/components/TrustTier'
import { ago, METHOD_NAMES } from '@/features/console/format'
import { Badge, Button, Card, type Column, DataTable, EmptyState, LoadingBlock, Notice } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

const RELATION: Record<string, string> = {
  manager: 'Manager',
  colleague: 'Colleague',
  external: 'External',
  unknown: 'Unknown',
}

export function People({ conn }: { conn: Connection }) {
  const { data, error, reload } = useApi<PersonRow[]>(conn, '/api/people')
  const [busy, setBusy] = useState<string | null>(null)
  const [choice, setChoice] = useState<Record<string, string>>({})

  async function unlinkIdentity(id: string) {
    setBusy(id)
    try {
      await api(conn, `/api/identities/${id}/unlink`, { method: 'POST' })
      await reload()
    } finally {
      setBusy(null)
    }
  }

  async function linkIdentity(id: string, personId: string) {
    setBusy(id)
    try {
      await api(conn, `/api/identities/${id}/link`, { method: 'POST', body: JSON.stringify({ person_id: personId }) })
      await reload()
    } finally {
      setBusy(null)
    }
  }

  if (error) return <Notice tone="danger">{error.message}</Notice>
  if (!data) return <LoadingBlock label="Loading people…" />
  if (data.length === 0)
    return (
      <EmptyState title="No one yet">
        Add yourself as the manager: <code>uv run superaide add-manager --name &quot;Your name&quot; --telegram-id 123</code>. Message the bot first if you
        don&apos;t know your ID; it will show up here as an unlinked account.
      </EmptyState>
    )

  const linkable = data.filter((x) => x.status !== 'stranger')

  function columns(p: PersonRow): Column<Identity>[] {
    return [
      {
        key: 'channel',
        header: 'Channel',
        width: '21%',
        render: (i) => (
          <>
            <span className="provider brand-label">
              {isBrand(i.provider) && <BrandIcon brand={i.provider} size={14} decorative />}
              {i.provider}
            </span>
            {i.display_name && <span className="quiet"> shows as &quot;{i.display_name}&quot;</span>}
            {i.risk_flags.includes('external') && (
              <Badge tone="danger" className="c-badge-inline">
                Outside company
              </Badge>
            )}
          </>
        ),
      },
      {
        key: 'id',
        header: 'Platform ID',
        width: '19%',
        render: (i) => <code>{i.tenant_scope ? `${i.tenant_scope}/${i.stable_key}` : i.stable_key}</code>,
      },
      { key: 'trust', header: 'Trust', width: '14%', render: (i) => <TrustTier level={i.trust_level} /> },
      { key: 'how', header: 'How it was linked', width: '15%', render: (i) => METHOD_NAMES[i.method] ?? i.method },
      { key: 'seen', header: 'Last seen', width: '8%', render: (i) => <span className="quiet">{ago(i.last_seen_at)}</span> },
      {
        key: 'actions',
        label: 'Actions',
        width: '23%',
        render: (i) =>
          i.method === 'account_signed_in' ? (
            <span className="quiet">Owner&apos;s own sign-in; cannot be unlinked</span>
          ) : p.status !== 'stranger' ? (
            <Button variant="ghost" size="sm" disabled={busy === i.id} onClick={() => unlinkIdentity(i.id)}>
              Unlink
            </Button>
          ) : i.risk_flags.includes('external') ? null : (
            <span className="link-to">
              <select
                aria-label="Person to link this account to"
                value={choice[i.id] ?? ''}
                onChange={(e) => setChoice((c) => ({ ...c, [i.id]: e.target.value }))}
              >
                <option value="">This is…</option>
                {linkable.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
              <Button variant="secondary" size="sm" disabled={!choice[i.id] || busy === i.id} onClick={() => linkIdentity(i.id, choice[i.id])}>
                Link
              </Button>
            </span>
          ),
      },
    ]
  }

  return (
    <>
      {data.map((p) => (
        <Card
          key={p.id}
          title={p.name}
          titleId={`person-${p.id}`}
          actions={
            <Badge tone={p.status === 'stranger' ? 'warn' : 'accent'}>
              {p.status === 'stranger' ? 'Not linked to anyone' : RELATION[p.relation] ?? p.relation}
            </Badge>
          }
        >
          {p.status === 'stranger' && (
            <Notice tone="warn">
              Is one of these you, messaging from your real Telegram or Slack? Pick your name and press Link. Only do this for accounts you know are yours:
              anyone else should prove it with a code.
            </Notice>
          )}
          <DataTable
            caption={`Channels for ${p.name}`}
            columns={columns(p)}
            rows={p.identities}
            rowKey={(i) => i.id}
            minWidth={880}
            empty={<p className="quiet">No channels linked yet.</p>}
          />
          {p.link_events.length > 0 && (
            <details className="c-details">
              <summary>Link history ({p.link_events.length})</summary>
              <ol className="history">
                {p.link_events.map((e, n) => (
                  <li key={n}>
                    {e.action === 'link' ? 'Linked' : 'Unlinked'}: {METHOD_NAMES[e.method] ?? e.method}, by {e.actor}, {ago(e.at)}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </Card>
      ))}
    </>
  )
}
