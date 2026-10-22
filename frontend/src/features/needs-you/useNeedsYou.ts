'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type NeedsYou } from '@/lib/api'

// The Needs-you inbox. `enabled` is false while the backend has triage turned off (it answers
// 404), so callers render nothing and the app looks exactly as it did before.
export function useNeedsYou(poll = 0) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<NeedsYou | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setData(await call<NeedsYou>('/needs-you'))
      setEnabled(true)
      setError(null)
    } catch (e) {
      if (e instanceof AppError && e.status === 404) {
        setEnabled(false)
        setData(null)
        return
      }
      setError(e instanceof AppError ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void reload()
    if (!poll) return
    const t = setInterval(() => void reload(), poll)
    return () => clearInterval(t)
  }, [reload, poll])

  return { enabled, data, error, reload }
}
