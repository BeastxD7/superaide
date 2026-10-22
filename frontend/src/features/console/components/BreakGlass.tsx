'use client'

// Break-glass (ADR 0009): an owner's people, conversations and log are theirs. To open them the
// operator names one hired Ava and gives a reason; every request is recorded in that owner's own
// log, which they can read in the app. Nothing here is saved: closing the tab closes access.
import { useState, type FormEvent } from 'react'
import type { Access, Connection, InstanceRow } from '@/features/console/api'
import { ago } from '@/features/console/format'
import { useApi } from '@/features/console/useApi'

const MIN_REASON = 10

export function BreakGlass({ conn, onOpen }: { conn: Connection; onOpen: (access: Access) => void }) {
  const instances = useApi<InstanceRow[]>(conn, '/api/instances', 30_000)
  const [instance, setInstance] = useState('')
  const [reason, setReason] = useState('')
  const chosen = instance || instances.data?.[0]?.id || ''
  const ready = reason.trim().length >= MIN_REASON

  function submit(e: FormEvent) {
    e.preventDefault()
    if (ready) onOpen({ reason: reason.trim(), instance: chosen })
  }

  return (
    <form className="break-glass" onSubmit={submit} aria-labelledby="break-glass-title">
      <h2 id="break-glass-title">This is an owner's private content</h2>
      <p className="quiet">
        Open it only to help with a problem. Say why: every page you open is recorded with your reason in that
        owner's own log, and they can read it.
      </p>
      {instances.error && <p className="error">{instances.error.message}</p>}
      <label>
        Which Ava
        <select value={chosen} onChange={(e) => setInstance(e.target.value)} disabled={!instances.data?.length}>
          {!instances.data?.length && <option value="">The server's first Ava (nobody has hired yet)</option>}
          {(instances.data ?? []).map((i) => (
            <option key={i.id} value={i.id}>
              {i.agent} · since {ago(i.since)} · {i.counts.memories} memories · {i.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Reason (at least {MIN_REASON} characters)
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          maxLength={500}
          placeholder="e.g. the owner reported a reminder that never arrived"
        />
      </label>
      <button className="primary" type="submit" disabled={!ready}>
        Open, and record it in the owner's log
      </button>
    </form>
  )
}
