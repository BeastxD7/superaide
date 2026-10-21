import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Connection } from '@/features/console/api'

// Fetches a path and refreshes it every `everyMs` while the tab is visible.
export function useApi<T>(conn: Connection, path: string, everyMs = 5000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const alive = useRef(true)

  const load = useCallback(async () => {
    try {
      const result = await api<T>(conn, path)
      if (alive.current) {
        setData(result)
        setError(null)
      }
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError ? e : new ApiError(0, String(e)))
    }
  }, [conn, path])

  useEffect(() => {
    alive.current = true
    load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, everyMs)
    return () => {
      alive.current = false
      window.clearInterval(timer)
    }
  }, [load, everyMs])

  return { data, error, reload: load }
}
