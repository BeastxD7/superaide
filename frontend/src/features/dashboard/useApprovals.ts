'use client'

import { useMemo } from 'react'
import type { ApprovalItem } from '@/lib/api'
import { useApprovalsChanged } from '@/lib/approvals-sync'
import { useResource } from '@/lib/use-resource'

// How often an open page asks again, so a request Ava is blocked on shows up without a reload (#294).
export const APPROVALS_POLL_MS = 20_000

// The approvals list, kept fresh: re-read on a timer, when another card decides something, and when the tab
// comes back. `waiting` is what the owner can still say yes to; `expired` ran out of time and can only be asked again.
export function useApprovals() {
  const { data, error, reload } = useResource<ApprovalItem[]>('/approvals', { poll: APPROVALS_POLL_MS })
  useApprovalsChanged(reload)
  const waiting = useMemo(() => (data ?? []).filter((a) => a.status === 'pending'), [data])
  const expired = useMemo(() => (data ?? []).filter((a) => a.status === 'expired'), [data])
  return { data, error, reload, waiting, expired }
}
