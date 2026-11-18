'use client'

import { useMemo } from 'react'
import type { ApprovalBatch, ApprovalItem } from '@/lib/api'
import { useApprovalsChanged } from '@/lib/approvals-sync'
import { useResource } from '@/lib/use-resource'

// How often an open page asks again, so a request Ava is blocked on shows up without a reload (#294).
export const APPROVALS_POLL_MS = 20_000

// The approvals list, kept fresh: re-read on a timer, when another card decides something, and when the tab
// comes back. `waiting` is what the owner can still say yes to; `expired` ran out of time and can only be asked again.
export function useApprovals() {
  const { data, error, reload } = useResource<ApprovalItem[]>('/approvals', { poll: APPROVALS_POLL_MS })
  // Groups of alike, safe requests (#408). The API answers 404 while batching is off: then there are none.
  const batchRes = useResource<ApprovalBatch[]>('/approvals/batches', { poll: APPROVALS_POLL_MS })
  const reloadBatches = batchRes.reload
  useApprovalsChanged(() => {
    reload()
    reloadBatches()
  })
  const waiting = useMemo(() => (data ?? []).filter((a) => a.status === 'pending'), [data])
  const expired = useMemo(() => (data ?? []).filter((a) => a.status === 'expired'), [data])
  // Only a group whose every item is still waiting here is shown as a batch; otherwise its items show one by one.
  const batches = useMemo(() => {
    const ids = new Set(waiting.map((a) => a.id))
    return (batchRes.data ?? []).filter((b) => b.items.length >= 2 && b.items.every((i) => ids.has(i.id)))
  }, [batchRes.data, waiting])
  const reloadAll = async () => {
    await Promise.all([reload(), reloadBatches()])
  }
  return { data, error, reload: reloadAll, waiting, expired, batches }
}
