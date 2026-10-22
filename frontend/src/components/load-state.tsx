'use client'

// One pattern for "couldn't load this" and one for "that action failed", used by the dashboard,
// Connections and the hire page so a failure never looks like an endless wait or a dead button.
import { useState, type ReactNode } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

const sentence = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`)

/** Placeholder bars while a request is in flight. Gone as soon as it settles, whichever way. */
export function Loading() {
  return (
    <div className="space-y-2" aria-busy="true">
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
    </div>
  )
}

/** "Couldn't load X" with a Retry button. `onRetry` may return a promise; the button waits for it. */
export function LoadError({ what = 'this', error, onRetry }: { what?: string; error: string; onRetry: () => unknown }) {
  const [busy, setBusy] = useState(false)
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-danger">
      <p className="min-w-0 flex-1">{`Couldn't load ${what}. ${sentence(error)}`}</p>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          void Promise.resolve(onRetry()).finally(() => setBusy(false))
        }}
      >
        Retry
      </Button>
    </div>
  )
}

export type LoadState<T> = { data: T | null; error: string | null; reload: () => unknown }

/** Renders `children(data)` once loaded, a LoadError if the request failed, otherwise the placeholder. */
export function Loadable<T>({
  state,
  what,
  children,
}: {
  state: LoadState<T>
  what?: string
  children: (data: T) => ReactNode
}) {
  if (state.data) return <>{children(state.data)}</>
  if (state.error) return <LoadError what={what} error={state.error} onRetry={state.reload} />
  return <Loading />
}

/** A failed action (save, disconnect, link …): say what failed and what to do. Renders nothing without an error. */
export function ActionError({ action, error }: { action: string; error: string | null }) {
  if (!error) return null
  return (
    <div role="alert">
      <Notice tone="error">{`Couldn't ${action}. ${sentence(error)} Try again.`}</Notice>
    </div>
  )
}
