'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type NeedsYou } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { useSession } from '@/lib/session-context'

// The Needs-you inbox. `enabled` is false while the backend has triage turned off, so callers render nothing
// and the app looks exactly as it did before. /app/me says so (`features.triage`), so the list is not even
// requested then (it would answer 404 by design and show as a failed request). An API that predates
// `features` is asked, and its 404 means the same thing.
export function useNeedsYou(poll = 0) {
  const { me } = useSession()
  const known = me !== null
  const off = me?.features?.triage === false
  const [asked, setEnabled] = useState<boolean | null>(null)
  const enabled = off ? false : asked
  const [data, setData] = useState<NeedsYou | null>(null)
  const [error, setError] = useState<string | null>(null)

  // State is set only once the request settles (written with .then so the effect lint can see that).
  const reload = useCallback(
    () =>
      !known || off
        ? Promise.resolve()
        : call<NeedsYou>('/needs-you').then(
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
    [known, off],
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
