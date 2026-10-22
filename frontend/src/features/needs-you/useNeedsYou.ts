'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type NeedsYou } from '@/lib/api'

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
          setError(e instanceof AppError ? e.message : String(e))
        },
      ),
    [],
  )

  useEffect(() => {
    void reload()
  }, [reload])

  // Poll only while triage is on: with it off, one 404 and nothing more.
  useEffect(() => {
    if (!poll || !enabled) return
    const t = setInterval(() => void reload(), poll)
    return () => clearInterval(t)
  }, [reload, poll, enabled])

  return { enabled, data, error, reload }
}
