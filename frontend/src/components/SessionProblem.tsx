'use client'

import { useState } from 'react'
import { Button, Notice } from '@/components/site'
import type { Session } from '@/lib/session-context'

// Shown instead of a redirect when the owner's account or their assistant's state could not be
// read (a network blip, a server error). The owner is signed in and hired; nothing is wrong with
// that, so they stay where they are and can try again.
export function SessionProblem({ session, dark = false }: { session: Session; dark?: boolean }) {
  const [busy, setBusy] = useState(false)

  async function retry() {
    setBusy(true)
    try {
      await session.refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`grid min-h-dvh place-items-center px-4 ${dark ? 'bg-sidebar' : 'mesh'}`}>
      <div className="w-full max-w-sm space-y-4 text-center" role="alert">
        <Notice tone="warn">Can&apos;t reach your assistant right now. Check your connection, then try again.</Notice>
        <Button tone="violet" onClick={() => void retry()} disabled={busy}>
          {busy ? 'Retrying…' : 'Retry'}
        </Button>
      </div>
    </div>
  )
}
