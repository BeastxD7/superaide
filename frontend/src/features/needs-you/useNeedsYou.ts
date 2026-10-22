'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type NeedsYou } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// The Needs-you inbox. `enabled` is false while the backend has triage turned off (it answers
// 404), so callers render nothing and the app looks exactly as it did before.
export function useNeedsYou(poll = 0) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<NeedsYou | null>(null)
  const [error, setError] = useState<string | null>(null)

  // State is set only once the request settles (written with .then so the effect lint can see that).
  const reload = useCallback(
    () =>
      call<NeedsYou>('/needs-you').then(
        (d) => {
          setData(d)
          setEnabled(true)
          setError(null)
        },
        (e: unknown) => {
          if (e instanceof AppError && e.status === 404) {
            setEnabled(false)
            setData(null)
            return
          }
          setError(errorMessage(e))
        },
      ),
    [],
  )

  useEffect(() => {
    void reload()
  }, [reload])

  // Poll unless triage is known to be off (a 404): a failed first call (502 during a restart,
  // offline) leaves `enabled` null and must not stop the polling, or the inbox stays hidden for
  // the whole session. A hidden tab doesn't poll.
  useEffect(() => {
    if (!poll || enabled === false) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, poll)
    return () => clearInterval(t)
  }, [reload, poll, enabled])

  return { enabled, data, error, reload }
}
