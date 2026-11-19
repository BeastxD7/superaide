'use client'

import { useEffect, useId, useRef } from 'react'

// The first-run prompt: take the tour, or skip it. Skip is as easy as take, and Escape skips.
export function WelcomeTour({ name, onTake, onSkip }: { name: string; onTake: () => void; onSkip: () => void }) {
  const titleId = useId()
  const bodyId = useId()
  const takeRef = useRef<HTMLButtonElement>(null)
  const skipRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const before = document.activeElement
    takeRef.current?.focus()
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSkip()
      } else if (e.key === 'Tab') {
        // Two buttons: keep focus on them.
        e.preventDefault()
        const other = document.activeElement === takeRef.current ? skipRef.current : takeRef.current
        other?.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      if (before instanceof HTMLElement && document.contains(before)) before.focus()
    }
  }, [onSkip])

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-night-900/60 p-4" data-testid="tour-welcome">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        className="w-full max-w-md rounded-2xl border border-line bg-white p-6 text-ink shadow-xl dark:border-white/15 dark:bg-night-900 dark:text-white"
      >
        <h2 id={titleId} className="text-xl font-semibold tight text-ink dark:text-white">
          Welcome. Want a quick tour?
        </h2>
        <p id={bodyId} className="mt-2 text-sm text-body dark:text-white/80">
          It takes about a minute. We will point at the parts of the app that matter, and show what {name} can do for you. You can skip it now and replay it
          later from Settings.
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={skipRef}
            type="button"
            onClick={onSkip}
            className="min-h-10 touch:min-h-11 rounded-lg border border-line px-4 text-sm hover:bg-mist focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:outline-none dark:border-white/20 dark:hover:bg-white/10"
          >
            Skip
          </button>
          <button
            ref={takeRef}
            type="button"
            onClick={onTake}
            className="min-h-10 touch:min-h-11 rounded-lg bg-violet-600 px-4 text-sm font-medium text-white hover:bg-violet-700 focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 focus-visible:outline-none"
          >
            Take the tour
          </button>
        </div>
      </div>
    </div>
  )
}
