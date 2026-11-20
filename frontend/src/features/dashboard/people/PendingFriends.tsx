'use client'

import { useState } from 'react'
import { ActionError } from '@/components/load-state'
import { Button } from '@/components/ui/button'
import { CHANNEL_LABEL, capitalise } from '@/features/dashboard/data'
import { errorMessage } from '@/lib/action'
import { call, type PendingFriend } from '@/lib/api'
import { useResource } from '@/lib/use-resource'
import { useSession } from '@/lib/session-context'

// Accounts that were acknowledged as a contact's but are not trusted yet. The platform's immutable id is what to
// check; the username and name are ones the sender chose, so they are labelled as changeable. Until the owner
// approves, the assistant shares only what is public with them.
export function PendingFriends() {
  const { me } = useSession()
  const { data, reload } = useResource<{ pending: PendingFriend[] }>('/friends/pending')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const name = me?.agent.name ?? 'Ava'
  if (!data || data.pending.length === 0) return null

  async function decide(id: string, verdict: 'approve' | 'decline') {
    setBusy(id)
    setError(null)
    try {
      await call(`/friends/${id}/${verdict}`, { method: 'POST' })
    } catch (e) {
      setError(errorMessage(e))
    }
    setBusy(null)
    await reload()
  }

  return (
    <section className="mt-6 rounded-xl border border-violet-200 bg-lilac-50 p-4" aria-labelledby="pending-friends">
      <h2 id="pending-friends" className="text-sm font-medium text-ink">
        Waiting for your approval
      </h2>
      <p className="mt-1 text-sm text-body">
        These accounts look like people on your list, but {name} can&apos;t be sure. Until you approve one, {name} shares
        only what is public with it and does not use their name.
      </p>
      <ActionError action="answer that" error={error} />
      <ul className="mt-3 space-y-3">
        {data.pending.map((p) => (
          <li key={p.identity_id} className="rounded-lg border border-line bg-white p-3">
            <p className="text-sm font-medium text-ink">Is this {p.contact.name}?</p>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted">{CHANNEL_LABEL[p.provider] ?? capitalise(p.provider)} id</dt>
              <dd className="font-mono text-ink [overflow-wrap:anywhere]">{p.platform_id}</dd>
              {p.username && (
                <>
                  <dt className="text-muted">Username</dt>
                  <dd className="text-body [overflow-wrap:anywhere]">@{p.username} (they chose this; it can change)</dd>
                </>
              )}
              {p.display_name && (
                <>
                  <dt className="text-muted">Shown name</dt>
                  <dd className="text-body [overflow-wrap:anywhere]">{p.display_name} (they chose this; anyone can copy it)</dd>
                </>
              )}
            </dl>
            <div className="mt-3 flex gap-2">
              <Button size="sm" disabled={busy === p.identity_id} onClick={() => void decide(p.identity_id, 'approve')}>
                Yes, approve this account
              </Button>
              <Button size="sm" variant="ghost" disabled={busy === p.identity_id} onClick={() => void decide(p.identity_id, 'decline')}>
                No, keep as a stranger
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
