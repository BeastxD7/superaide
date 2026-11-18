'use client'

import { Check, Layers, X } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { call, type ApprovalBatch } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Results = { results: { id: string; status: string; result: string | null }[] }

// Several alike, safe requests (your own calendar, nobody else told) as ONE card with every item listed and one
// decision (#408). The yes carries the digest of what is shown: if any item changed, nothing is decided and the
// card is reread. Each item is still bound and run on its own by the server.
export function BatchCard({
  batch,
  onDone,
  onError,
}: {
  batch: ApprovalBatch
  onDone: () => Promise<void>
  onError: (message: string | null) => void
}) {
  const [busy, setBusy] = useState(false)

  async function decide(verdict: 'approve' | 'reject') {
    setBusy(true)
    onError(null)
    try {
      const r = await call<Results>(`/approvals/batch/${verdict}`, {
        method: 'POST',
        json: { ids: batch.items.map((i) => i.id), digest: batch.digest },
      })
      const failed = r.results.filter((x) => verdict === 'approve' && x.status !== 'approved')
      if (failed.length) onError(failed.map((f) => f.result).filter(Boolean).join(' ') || 'Some requests were not approved.')
    } catch (e) {
      onError(errorMessage(e))
    } finally {
      await onDone()
      setBusy(false)
    }
  }

  return (
    <li className="rounded-xl border border-violet-200 bg-violet-50/40 p-3" data-testid="approval-batch">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-sm font-medium text-ink">
            <Layers className="size-4 text-violet-600" aria-hidden /> {batch.label}
            <Badge variant="secondary">{batch.items.length}</Badge>
          </p>
          <p className="mt-0.5 text-xs text-muted">
            Your own calendar, nobody else is told. Your yes covers exactly the {batch.items.length} below; if any changes, nothing happens.
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void decide('reject')}>
            <X aria-hidden /> No to all
          </Button>
          <Button size="sm" disabled={busy} onClick={() => void decide('approve')}>
            <Check aria-hidden /> Yes to all {batch.items.length}
          </Button>
        </div>
      </div>
      <details className="mt-2" open>
        <summary className="cursor-pointer text-xs font-medium text-violet-700">Preview every item</summary>
        <ul className="mt-1 space-y-1" data-testid="approval-batch-items">
          {batch.items.map((i) => (
            <li key={i.id} className="break-words rounded-md border border-line bg-white px-2 py-1 text-sm text-body">
              {i.summary}
            </li>
          ))}
        </ul>
      </details>
    </li>
  )
}
