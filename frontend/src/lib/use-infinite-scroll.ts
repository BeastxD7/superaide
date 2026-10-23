'use client'

import { useEffect, useRef } from 'react'

// Call `onNear` when the sentinel element comes within `margin` px of the viewport. Optional: ListFooter's
// Load more button stays rendered as the keyboard and no-JS-observer path. The controller ignores repeat calls
// while a request is running, so the observer firing twice costs nothing.
export function useInfiniteScroll<E extends HTMLElement>(onNear: () => void, { enabled = true, margin = 400 } = {}) {
  const ref = useRef<E | null>(null)
  const latest = useRef(onNear)
  useEffect(() => {
    latest.current = onNear
  })
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && latest.current(), {
      rootMargin: `0px 0px ${margin}px 0px`,
    })
    io.observe(el)
    return () => io.disconnect()
  }, [enabled, margin])
  return ref
}
