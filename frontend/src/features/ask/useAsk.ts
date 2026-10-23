'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// One owner endpoint of the "Ask my Ava" link. `enabled` is false while the backend has the feature
// off (it answers 404), so callers render nothing and the dashboard looks as it did before.
export function useAsk<T>(path: string) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(
    () =>
      call<T>(path).then(
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
    [path],
  )

  useEffect(() => {
    void reload()
  }, [reload])

  return { enabled, data, setData, error, reload }
}
