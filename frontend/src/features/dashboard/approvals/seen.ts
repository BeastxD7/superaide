import { call } from '@/lib/api'

// How long a card has to stay on screen before it counts as looked at (#407). Only a time is sent, never the card.
export const SEEN_AFTER_MS = 800
const told = new Set<string>()

// A ref callback for a card's element: once at least half of it has been visible for SEEN_AFTER_MS, tell the app
// once that this card was opened. A card scrolled past or hidden in another tab does not count. Failures are
// ignored: the measure must never get in the way of answering.
export function seenBeacon(id: string) {
  return (el: HTMLElement | null) => {
    if (!el || told.has(id) || typeof IntersectionObserver === 'undefined') return
    let timer: number | undefined
    const observer = new IntersectionObserver(
      ([entry]) => {
        window.clearTimeout(timer)
        if (entry?.isIntersecting && document.visibilityState === 'visible') {
          timer = window.setTimeout(() => {
            if (told.has(id)) return
            told.add(id)
            void call(`/approvals/${id}/opened`, { method: 'POST' }).catch(() => told.delete(id))
            observer.disconnect()
          }, SEEN_AFTER_MS)
        }
      },
      { threshold: 0.5 },
    )
    observer.observe(el)
    return () => {
      window.clearTimeout(timer)
      observer.disconnect()
    }
  }
}
