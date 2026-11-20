'use client'

import { useState } from 'react'
import { call } from '@/lib/api'
import { announceApprovalsChanged } from '@/lib/approvals-sync'
import { errorMessage } from '@/lib/action'

export const KIND_LABEL: Record<string, string> = {
  publish_post: 'Publish a post',
  message_someone_else: 'Message someone for you',
  gmail_send: 'Send an email from your Gmail',
  grant_access: 'Grant access',
  delete_memory: 'Delete something she remembers',
  ask_reply: 'Reply to someone from your Ask link',
}

// Deciding one request: the tick on an email's sign-off, and Yes or No. Shared by the card on Home and the Approvals page.
export function useApprovalDecisions(reload: () => Promise<void>) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [ticks, setTicks] = useState<Record<string, boolean>>({}) // the tick as clicked, until the saved answer is back
  // The tick on an email's card: the choice is saved (and sealed with the approval) before any yes.
  async function setSign(id: string, sign: boolean) {
    setTicks((t) => ({ ...t, [id]: sign }))
    setBusy(id)
    setError(null)
    try {
      await call(`/approvals/${id}/signature`, { method: 'POST', json: { sign } })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      await reload()
      setTicks((t) => {
        const { [id]: _gone, ...rest } = t
        return rest
      })
      setBusy(null)
    }
  }

  async function decide(id: string, verdict: 'approve' | 'reject') {
    setBusy(id)
    setError(null)
    try {
      const r = await call<{ status: string; result: string | null }>(`/approvals/${id}/${verdict}`, {
        method: 'POST',
        json: { reason: reasons[id] ?? null },
      })
      // A yes can come back refused (it ran out of time while the card was open): say so, do not just drop the card.
      if (verdict === 'approve' && r.status === 'rejected' && r.result) setError(r.result)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      // Read again even when it failed: "already decided" means this list was stale.
      await reload()
      announceApprovalsChanged()
      setBusy(null)
    }
  }

  return { busy, error, setError, reasons, setReasons, ticks, setSign, decide }
}
