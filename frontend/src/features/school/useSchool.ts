'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type SchoolList } from '@/lib/api'

// The lessons, and whether Ava School is turned on. The backend answers 404 while it is off, so callers
// render nothing and the app looks exactly as it did before.
export function useSchool() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [data, setData] = useState<SchoolList | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(
    () =>
      call<SchoolList>('/school').then(
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
  return { enabled, data, error, reload }
}

export function useSchoolEnabled(): boolean {
  return useSchool().enabled === true
}
