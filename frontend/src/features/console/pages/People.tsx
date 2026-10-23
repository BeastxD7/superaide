'use client'

import { useState } from 'react'
import { api, type Connection, type PersonRow } from '@/features/console/api'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { TrustTier } from '@/features/console/components/TrustTier'
import { ago, METHOD_NAMES } from '@/features/console/format'
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

  if (error) return <p className="error">{error.message}</p>
  if (!data) return <p className="quiet">Loading people…</p>
  if (data.length === 0)
    return (
      <div className="empty">
        <h2>No one yet</h2>
        <p>
          Add yourself as the manager: <code>uv run superaide add-manager --name "Your name" --telegram-id 123</code>.
          Message the bot first if you don't know your ID; it will show up here as an unlinked account.
        </p>
      </div>
    )

  return (
    <div className="people">
      {data.map((p) => (
        <section key={p.id} className={p.status === 'stranger' ? 'person stranger' : 'person'}>
          <header>
            <h2>{p.name}</h2>
            <span className="relation">{p.status === 'stranger' ? 'Not linked to anyone' : RELATION[p.relation] ?? p.relation}</span>
          </header>
          {p.status === 'stranger' && (
            <p className="quiet hint">
              Is one of these you, messaging from your real Telegram or Slack? Pick your name and press Link. Only do this for
              accounts you know are yours: anyone else should prove it with a code.
            </p>
          )}
          <table className="legacy-table">
            <thead>
              <tr>
                <th>Channel</th>
                <th>Platform ID</th>
                <th>Trust</th>
                <th>How it was linked</th>
                <th>Last seen</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {p.identities.map((i) => (
                <tr key={i.id}>
                  <td>
                    <span className="provider brand-label">
                      {isBrand(i.provider) && <BrandIcon brand={i.provider} size={14} decorative />}
                      {i.provider}
                    </span>
                    {i.display_name && <span className="quiet"> shows as "{i.display_name}"</span>}
                    {i.risk_flags.includes('external') && <span className="badge refused">Outside company</span>}
                  </td>
                  <td>
                    <code>{i.tenant_scope ? `${i.tenant_scope}/${i.stable_key}` : i.stable_key}</code>
                  </td>
                  <td>
                    <TrustTier level={i.trust_level} />
                  </td>
                  <td>{METHOD_NAMES[i.method] ?? i.method}</td>
                  <td className="quiet">{ago(i.last_seen_at)}</td>
                  <td>
                    {i.method === 'account_signed_in' ? (
                      <span className="quiet">Owner&apos;s own sign-in; cannot be unlinked</span>
                    ) : p.status !== 'stranger' ? (
                      <button className="link-button" disabled={busy === i.id} onClick={() => unlinkIdentity(i.id)}>
                        Unlink
                      </button>
                    ) : i.risk_flags.includes('external') ? null : (
                      <span className="link-to">
                        <select
                          aria-label="Person to link this account to"
                          value={choice[i.id] ?? ''}
                          onChange={(e) => setChoice((c) => ({ ...c, [i.id]: e.target.value }))}
                        >
                          <option value="">This is…</option>
                          {data
                            .filter((x) => x.status !== 'stranger')
                            .map((x) => (
                              <option key={x.id} value={x.id}>
                                {x.name}
                              </option>
                            ))}
                        </select>
                        <button
                          className="link-button"
                          disabled={!choice[i.id] || busy === i.id}
                          onClick={() => linkIdentity(i.id, choice[i.id])}
                        >
                          Link
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {p.link_events.length > 0 && (
            <details>
              <summary>Link history ({p.link_events.length})</summary>
              <ol className="history">
                {p.link_events.map((e, n) => (
                  <li key={n}>
                    {e.action === 'link' ? 'Linked' : 'Unlinked'}: {METHOD_NAMES[e.method] ?? e.method}, by {e.actor},{' '}
                    {ago(e.at)}
                  </li>
                ))}
              </ol>
            </details>
          )}
        </section>
      ))}
    </div>
  )
}
