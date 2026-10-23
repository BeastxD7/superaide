'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// One way to load an /app endpoint: data, an error written for the person, a reload, optional polling and
// an optional "a 404 means this feature is off" mode (`enabled`). Each page-level hook is a thin wrapper.
//
// `path` null means "not now" (nothing is requested). State is set only once a request settles, written with
// .then so the effect lint can see that. Polling skips hidden tabs and never stops after a failed call.
export type ResourceOptions = { poll?: number; offOn404?: boolean }

export function useResource<T>(path: string | null, { poll = 0, offOn404 = false }: ResourceOptions = {}) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [enabled, setEnabled] = useState<boolean | null>(null)

  const reload = useCallback(
    () =>
      path === null
        ? Promise.resolve()
        : call<T>(path).then(
            (d) => {
              setData(d)
              setEnabled(true)
              setError(null)
            },
            (e: unknown) => {
              if (offOn404 && e instanceof AppError && e.status === 404) {
                setEnabled(false)
                setData(null)
                return
              }
              setError(errorMessage(e))
            },
          ),
    [path, offOn404],
  )

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (!poll || path === null || enabled === false) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, poll)
    return () => clearInterval(t)
  }, [reload, poll, path, enabled])

  return { data, setData, error, enabled, reload }
}
