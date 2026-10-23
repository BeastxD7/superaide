import { useEffect, useRef } from 'react'

// Refresh a paged list now and then while the tab is visible (ADR 0014). A refresh reloads page one, so it is skipped
// once the person has loaded further pages (their place would jump) or while a request is already running.
export function usePoll(
  list: { items: unknown[]; status: string; refresh: () => Promise<void> },
  everyMs: number,
  pageSize: number,
) {
  const latest = useRef(list)
  useEffect(() => {
    latest.current = list
  })
  useEffect(() => {
    const t = window.setInterval(() => {
      const l = latest.current
      if (document.visibilityState !== 'visible') return
      if (l.status !== 'ready' || l.items.length > pageSize) return
      void l.refresh()
    }, everyMs)
    return () => window.clearInterval(t)
  }, [everyMs, pageSize])
}
