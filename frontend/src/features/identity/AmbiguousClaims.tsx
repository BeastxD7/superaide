'use client'

import { useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useApprovals } from '@/features/dashboard/approvals/useApprovals'
import { call, type ApprovalItem } from '@/lib/api'
import { announceApprovalsChanged } from '@/lib/approvals-sync'
import { errorMessage } from '@/lib/action'
import { formatWhen } from '@/lib/dates'

export const AMBIGUOUS_KIND = 'identity_claim_pick'

// Someone said a full name that two or more of your contacts have (#492). Ava sent nothing to anyone and told them
// nothing different; she asks you which contact it is. Yes sends a one-time code to that contact's own linked
// channel; no on every card ignores the claim. Renders nothing when there is nothing to answer.
export function AmbiguousClaims({ className }: { className?: string }) {
  const { waiting, reload } = useApprovals()
  const [busy, setBusy] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const items = waiting.filter((a) => a.kind === AMBIGUOUS_KIND)
  if (items.length === 0) return null

  async function decide(a: ApprovalItem, verdict: 'approve' | 'reject') {
    setBusy(a.id)
    setProblem(null)
    try {
      await call(`/approvals/${a.id}/${verdict}`, { method: 'POST', json: {} })
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(null)
      await reload()
      announceApprovalsChanged()
    }
  }

  return (
    <Card className={className} data-testid="ambiguous-claims">
      <CardHeader>
        <CardTitle>Which contact is this?</CardTitle>
        <CardDescription>
          Someone gave a name that more than one of your contacts has. Nothing was sent to anyone, and they were told
          nothing different. Pick the right contact and a one-time code goes to that contact&rsquo;s own channel. Say no
          on every card to ignore it.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {problem && <Notice tone="error">{problem}</Notice>}
        <ul className="grid gap-3">
          {items.map((a) => (
            <li key={a.id} className="rounded-xl border border-line p-3" data-testid="ambiguous-claim">
              <p className="text-sm text-ink">{a.summary}</p>
              <p className="mt-1 text-xs text-muted">{formatWhen(a.at)}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" disabled={busy === a.id} onClick={() => void decide(a, 'approve')}>
                  Yes, send a code to this one
                </Button>
                <Button size="sm" variant="outline" disabled={busy === a.id} onClick={() => void decide(a, 'reject')}>
                  No, not this one
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
