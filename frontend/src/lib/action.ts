'use client'

import { useCallback, useState } from 'react'
import { AppError } from '@/lib/api'

/** What to show a person for a failed request: the API's own words, or a plain line when it never answered. */
export function errorMessage(e: unknown): string {
  return e instanceof AppError ? e.message : 'We could not reach the server. Check your connection.'
}

/** Run an action with a busy flag and an error to show: `run(fn)` never throws. Returns true if it worked. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = useCallback(async (fn: () => Promise<void>): Promise<boolean> => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      return true
    } catch (e) {
      setError(errorMessage(e))
      return false
    } finally {
      setBusy(false)
    }
  }, [])
  const clear = useCallback(() => setError(null), [])
  return { run, busy, error, clear }
}
