import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type Connection } from '@/features/console/api'

// Fetches a path and refreshes it every `everyMs` while the tab is visible.
export function useApi<T>(conn: Connection, path: string, everyMs = 5000) {
  const [state, setState] = useState<{ path: string; data: T | null; error: ApiError | null }>({
    path,
    data: null,
    error: null,
  })
  // Only the newest request may write: a slow answer for the previous path (or connection) must
  // never land on the new one, which would show another owner's messages under it.
  const latest = useRef(0)

  const invalidate = () => {
    latest.current += 1
  }

  const load = useCallback(() => {
    const id = ++latest.current
    return api<T>(conn, path).then(
      (result) => {
        if (id === latest.current) setState({ path, data: result, error: null })
      },
      (e) => {
        if (id === latest.current)
          setState((s) => ({ path, data: s.path === path ? s.data : null, error: e instanceof ApiError ? e : new ApiError(0, String(e)) }))
      },
    )
  }, [conn, path])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, everyMs)
    return () => {
      invalidate() // anything still in flight is stale now
      window.clearInterval(timer)
    }
  }, [load, everyMs])  // eslint-disable-line react-hooks/exhaustive-deps

  // Data from another path is never shown: the new path reads as loading until it answers.
  const current = state.path === path
  return { data: current ? state.data : null, error: current ? state.error : null, reload: load }
}
