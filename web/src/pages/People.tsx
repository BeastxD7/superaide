import { useState } from 'react'
import { api, type Connection, type PersonRow } from '../api'
import { TrustTier } from '../components/TrustTier'
import { ago, METHOD_NAMES } from '../format'
import { useApi } from '../useApi'

const RELATION: Record<string, string> = {
  manager: 'Manager',
  colleague: 'Colleague',
  external: 'External',
  unknown: 'Unknown',
}

export function People({ conn }: { conn: Connection }) {
  const { data, error, reload } = useApi<PersonRow[]>(conn, '/api/people')
  const [busy, setBusy] = useState<string | null>(null)

  async function unlinkIdentity(id: string) {
    setBusy(id)
    try {
      await api(conn, `/api/identities/${id}/unlink`, { method: 'POST' })
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
          Add yourself as the manager: <code>uv run agi-agent add-manager --name "Your name" --telegram-id 123</code>.
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
          <table>
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
                    <span className="provider">{i.provider}</span>
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
                    {p.status !== 'stranger' && (
                      <button className="link-button" disabled={busy === i.id} onClick={() => unlinkIdentity(i.id)}>
                        Unlink
                      </button>
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
