'use client'

import { KeyRound } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'

type Mandate = {
  id: string
  parent_id: string | null
  label: string
  card: string
  status: 'active' | 'revoked'
  uses: number
  expires_at: string
  depth: number
  chain: string
}

// Mandate cards: the named, limited things Ava may do without asking, and who each was handed on to, narrower each
// time. Shown only while the feature is on (the API answers 404 when off). Revoking one revokes what was handed on from it.
export function MandatesCard({ name }: { name: string }) {
  const { data, error, reload } = useAppData<{ mandates: Mandate[] }>('/mandates')
  const [busy, setBusy] = useState<string | null>(null)
  if (error || !data) return null
  const active = data.mandates.filter((m) => m.status === 'active')

  async function revoke(id: string) {
    setBusy(id)
    try {
      await call(`/mandates/${id}/revoke`, { method: 'POST' })
      await reload()
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-5 text-violet-600" aria-hidden />
          What {name} may do without asking
          <Badge variant="secondary">{active.length}</Badge>
        </CardTitle>
        <CardDescription>
          Each card is limited and expires. A helper can only be given the same or less, never more. Sending and spending always ask you.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {active.length === 0 ? (
          <p className="text-sm text-muted">No mandates are active.</p>
        ) : (
          <ul className="space-y-2">
            {active.map((m) => (
              <li key={m.id} className="flex items-start gap-3 rounded-xl border border-line bg-white p-3" style={{ marginLeft: m.depth * 16 }}>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">{m.depth > 0 ? `${m.label || 'sub-task'} (handed on)` : m.label || 'Mandate'}</p>
                  <p className="mt-0.5 break-words text-sm text-body">{m.card}</p>
                  {m.depth > 0 && <p className="mt-1 break-words text-xs text-violet-700">{m.chain}</p>}
                  <p className="mt-1 text-xs text-muted">
                    Used {m.uses} time{m.uses === 1 ? '' : 's'} · until {new Date(m.expires_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
                  </p>
                </div>
                <Button size="sm" variant="outline" disabled={busy === m.id} onClick={() => void revoke(m.id)}>
                  Revoke{data.mandates.some((c) => c.parent_id === m.id && c.status === 'active') ? ' all' : ''}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
