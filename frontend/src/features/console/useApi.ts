import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Connection } from '@/features/console/api'

// Fetches a path and refreshes it every `everyMs` while the tab is visible.
export function useApi<T>(conn: Connection, path: string, everyMs = 5000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const alive = useRef(true)

  // State is set only once the request settles (written with .then so the effect lint can see that).
  const load = useCallback(
    () =>
      api<T>(conn, path).then(
        (result) => {
          if (alive.current) {
            setData(result)
            setError(null)
          }
        },
        (e) => {
          if (alive.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e)))
        },
      ),
    [conn, path],
  )

  useEffect(() => {
    alive.current = true
    void load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, everyMs)
    return () => {
      alive.current = false
      window.clearInterval(timer)
    }
  }, [load, everyMs])

  return { data, error, reload: load }
}
