'use client'

import { Button } from '@/components/ui/button'
import { useAction } from '@/lib/action'
import { call } from '@/lib/api'
import { useSession } from '@/lib/session-context'

/** Lifts a pause that is the owner's own (the halt word, a safety limit). Asks the API, then refreshes the app. */
export function ResumeButton({ name }: { name: string }) {
  const { refresh } = useSession()
  const { busy, error, run } = useAction()
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await call('/resume', { method: 'POST' })
            await refresh()
          })
        }
      >
        {busy ? 'Resuming…' : `Resume ${name}`}
      </Button>
      {error && <span role="alert">{error}</span>}
    </span>
  )
}
