'use client'

import type { Connection, Spend, SpendEntry } from '@/features/console/api'
import { ago, usd } from '@/features/console/format'
import { useApi } from '@/features/console/useApi'

export function SpendPage({ conn }: { conn: Connection }) {
  const { data, error } = useApi<{ summary: Spend; entries: SpendEntry[] }>(conn, '/api/spend')
  if (error) return <p className="error">{error.message}</p>
  if (!data) return <p className="quiet">Loading spend…</p>
  const { summary, entries } = data
  return (
    <div className="spend-page">
      <p className="lede">
        {usd(summary.settled_usd)} spent of a {usd(summary.cap_usd, 2)} cap across {summary.calls} calls.{' '}
        {usd(summary.remaining_usd)} left. Each call reserves its worst-case price first and is refused if that could pass
        the cap.
      </p>
      {entries.length === 0 ? (
        <div className="empty">
          <h2>No model calls yet</h2>
          <p>
            Run <code>uv run superaide hello</code> for one tiny real call (well under a cent) to check your API key.
          </p>
        </div>
      ) : (
        <table className="spend-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Purpose</th>
              <th>Model</th>
              <th className="r">Input</th>
              <th className="r">Cached</th>
              <th className="r">Output</th>
              <th className="r">Cost</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} className={e.status !== 'settled' ? 'muted' : ''}>
                <td className="quiet">{ago(e.at)}</td>
                <td>{e.purpose.replaceAll('_', ' ')}</td>
                <td>
                  <code>{e.model}</code>
                </td>
                <td className="r num">{e.input_tokens.toLocaleString()}</td>
                <td className="r num">{e.cache_read_tokens.toLocaleString()}</td>
                <td className="r num">{e.output_tokens.toLocaleString()}</td>
                <td className="r num">{e.status === 'settled' ? usd(e.cost_usd, 5) : e.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
