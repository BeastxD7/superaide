'use client'

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { findTarget } from '@/features/tour/dom'
import { popoverPosition, spotlight, type Box, type TourStep } from '@/features/tour/tours'

const PAD = 8

function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// The spotlight walkthrough: a dimmed page with a hole over the real element, and a popover that explains it.
// Keyboard: Right/Enter on the button = next, Left = back, Escape = skip, Tab stays inside the popover.
export function TourOverlay({
  steps,
  onFinish,
  onSkip,
}: {
  steps: TourStep[]
  onFinish: () => void
  onSkip: () => void
}) {
  const [index, setIndex] = useState(0)
  const [hole, setHole] = useState<Box | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const primaryRef = useRef<HTMLButtonElement>(null)
  const returnTo = useRef<Element | null>(null)
  const titleId = useId()
  const bodyId = useId()
  const step = steps[index]
  const last = index === steps.length - 1

  const next = useCallback(() => (last ? onFinish() : setIndex((i) => i + 1)), [last, onFinish])
  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), [])

  // Remember where focus was, and give it back when the tour ends.
  useEffect(() => {
    returnTo.current = document.activeElement
    return () => {
      if (returnTo.current instanceof HTMLElement && document.contains(returnTo.current)) returnTo.current.focus()
    }
  }, [])

  // Measure the target now and whenever the page moves under it.
  useLayoutEffect(() => {
    const el = findTarget(step.target)
    if (!el) return
    el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' })
    let frame = 0
    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const target = findTarget(step.target)
        if (!target) return
        const r = target.getBoundingClientRect()
        const h = spotlight({ top: r.top, left: r.left, width: r.width, height: r.height }, PAD, window.innerWidth, window.innerHeight)
        setHole(h)
        const pop = popRef.current
        if (pop) setPos(popoverPosition(h, { width: pop.offsetWidth, height: pop.offsetHeight }, window.innerWidth, window.innerHeight))
      })
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    // Smooth scrolling and late layout settle a moment after the first measure.
    const settle = window.setTimeout(measure, 350)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(settle)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [step])

  useEffect(() => {
    primaryRef.current?.focus()
  }, [index])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSkip()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        next()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        back()
      } else if (e.key === 'Tab') {
        const buttons = [...(popRef.current?.querySelectorAll<HTMLElement>('button:not([disabled])') ?? [])]
        if (buttons.length === 0) return
        const first = buttons[0]
        const lastB = buttons[buttons.length - 1]
        const active = document.activeElement
        if (!popRef.current?.contains(active)) {
          e.preventDefault()
          first.focus()
        } else if (e.shiftKey && active === first) {
          e.preventDefault()
          lastB.focus()
        } else if (!e.shiftKey && active === lastB) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [next, back, onSkip])

  return (
    <div data-testid="tour-overlay">
      {/* Swallows clicks, so the page underneath is only looked at, never changed, during the tour. */}
      <div className="fixed inset-0 z-[60]" aria-hidden />
      {hole && (
        <div
          aria-hidden
          data-testid="tour-spotlight"
          className="pointer-events-none fixed z-[61] rounded-xl ring-2 ring-violet-400 shadow-[0_0_0_9999px_rgba(18,14,42,0.62)] transition-all duration-200 motion-reduce:transition-none"
          style={{ top: hole.top, left: hole.left, width: hole.width, height: hole.height }}
        />
      )}
      <div
        ref={popRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        data-testid="tour-popover"
        className="fixed z-[62] w-[min(22rem,calc(100vw-1rem))] rounded-2xl border border-line bg-white p-4 text-ink shadow-xl dark:border-white/15 dark:bg-night-900 dark:text-white"
        style={{ top: pos?.top ?? 8, left: pos?.left ?? 8, visibility: pos ? 'visible' : 'hidden' }}
      >
        <p className="text-xs text-muted dark:text-white/70" aria-live="polite">
          Step {index + 1} of {steps.length}
        </p>
        <h2 id={titleId} className="mt-1 text-base font-semibold text-ink dark:text-white">
          {step.title}
        </h2>
        <p id={bodyId} className="mt-1 text-sm text-body dark:text-white/80">
          {step.body}
        </p>
        <div className="mt-3 flex items-center gap-1.5" aria-hidden>
          {steps.map((_, i) => (
            <span
              key={i}
              data-testid="tour-dot"
              data-active={i === index}
              className={`size-1.5 rounded-full ${i === index ? 'bg-violet-600 dark:bg-violet-400' : 'bg-line dark:bg-white/25'}`}
            />
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={onSkip}
            className="min-h-9 touch:min-h-11 rounded-lg px-2 text-sm text-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:outline-none dark:text-white/70 dark:hover:text-white"
          >
            Skip tour
          </button>
          <div className="flex gap-2">
            {index > 0 && (
              <button
                type="button"
                onClick={back}
                className="min-h-9 touch:min-h-11 rounded-lg border border-line px-3 text-sm hover:bg-mist focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:outline-none dark:border-white/20 dark:hover:bg-white/10"
              >
                Back
              </button>
            )}
            <button
              ref={primaryRef}
              type="button"
              onClick={next}
              className="min-h-9 touch:min-h-11 rounded-lg bg-violet-600 px-3 text-sm font-medium text-white hover:bg-violet-700 focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 focus-visible:outline-none"
            >
              {last ? 'Done' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
