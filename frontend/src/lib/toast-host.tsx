'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { TOAST_EVENT, type ToastItem } from '@/lib/toast'

// A polite live region, no animation (so reduced motion is respected by having none), gone after a few seconds
// unless the pointer or focus is on it.
const SHOW_MS = 7_000
const MAX_SHOWN = 3

export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id))
    timers.current.delete(id)
    setItems((all) => all.filter((t) => t.id !== id))
  }, [])

  const arm = useCallback(
    (item: ToastItem) => {
      window.clearTimeout(timers.current.get(item.id))
      timers.current.set(item.id, window.setTimeout(() => dismiss(item.id), item.ms ?? SHOW_MS))
    },
    [dismiss],
  )

  useEffect(() => {
    const onToast = (e: Event) => {
      const item = (e as CustomEvent<ToastItem>).detail
      setItems((all) => [...all.slice(-(MAX_SHOWN - 1)), item])
      arm(item)
    }
    window.addEventListener(TOAST_EVENT, onToast)
    const live = timers.current
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast)
      live.forEach((t) => window.clearTimeout(t))
      live.clear()
    }
  }, [arm])

  return (
    <div
      role="region"
      aria-label="Notifications"
      className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 md:inset-x-auto md:top-16 md:right-4 md:bottom-auto md:items-end"
    >
      {/* The live region stays mounted (and empty) so a screen reader hears each message as it is added. */}
      <div role="status" aria-live="polite" className="flex flex-col items-center gap-2 md:items-end">
        {items.map((t) => (
          <div
            key={t.id}
            data-testid="toast"
            onMouseEnter={() => window.clearTimeout(timers.current.get(t.id))}
            onMouseLeave={() => arm(t)}
            onFocus={() => window.clearTimeout(timers.current.get(t.id))}
            className="pointer-events-auto flex max-w-sm items-start gap-3 rounded-xl border border-line bg-white p-3 text-sm text-ink shadow-lg"
          >
            <p className="min-w-0 flex-1 [overflow-wrap:anywhere]">{t.message}</p>
            <div className="flex shrink-0 items-center gap-1">
              {t.actions?.map((a) =>
                a.href ? (
                  <Link
                    key={a.label}
                    href={a.href}
                    onClick={() => dismiss(t.id)}
                    className="rounded-md px-2 py-1 font-medium text-violet-700 underline-offset-2 hover:underline"
                  >
                    {a.label}
                  </Link>
                ) : (
                  <button
                    key={a.label}
                    type="button"
                    onClick={() => {
                      dismiss(t.id)
                      void a.onClick?.()
                    }}
                    className="rounded-md px-2 py-1 font-medium text-violet-700 underline-offset-2 hover:underline"
                  >
                    {a.label}
                  </button>
                ),
              )}
              <button type="button" aria-label="Dismiss" onClick={() => dismiss(t.id)} className="rounded-md p-1 text-muted hover:text-ink">
                <X className="size-4" aria-hidden />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
