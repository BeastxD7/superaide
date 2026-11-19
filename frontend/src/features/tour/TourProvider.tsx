'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { call } from '@/lib/api'
import { findTarget } from '@/features/tour/dom'
import { TourContext, type TourApi } from '@/features/tour/tour-context'
import { TourOverlay } from '@/features/tour/TourOverlay'
import { WelcomeTour } from '@/features/tour/WelcomeTour'
import { autoStart, fill, TOURS, tourForPath, type PageTourId, type Progress, type TourId, type TourStep } from '@/features/tour/tours'

async function save(tour: TourId, status: 'completed' | 'skipped'): Promise<void> {
  try {
    await call(`/tour/${tour}`, { method: 'POST', json: { status } })
  } catch {
    // The tour is a nicety: if saving fails it simply shows again next time.
  }
}

// The tour for the signed-in app. `ready`: the introduction is done, so the dashboard is what the owner is looking at.
export function TourProvider({ ready, agentName, children }: { ready: boolean; agentName: string; children: ReactNode }) {
  const pathname = usePathname() ?? '/dashboard'
  const router = useRouter()
  const [progress, setProgress] = useState<Progress | null>(null)
  const [welcome, setWelcome] = useState(false)
  const [run, setRun] = useState<{ id: PageTourId; steps: TourStep[] } | null>(null)
  const tried = useRef(new Set<string>())
  const pageTour = tourForPath(pathname)
  const busy = welcome || run !== null

  useEffect(() => {
    if (!ready) return
    let alive = true
    call<{ tours: Progress }>('/tour').then(
      (r) => alive && setProgress(r.tours),
      () => undefined, // can't tell what was seen: show nothing rather than nag
    )
    return () => {
      alive = false
    }
  }, [ready])

  const begin = useCallback(
    (id: PageTourId) => {
      const steps = TOURS[id].steps.filter((s) => findTarget(s.target)).map((s) => ({ ...s, title: fill(s.title, agentName), body: fill(s.body, agentName) }))
      if (steps.length > 0) setRun({ id, steps })
    },
    [agentName],
  )

  // Start by itself: the welcome prompt once, then each page's tour the first time the owner opens it.
  useEffect(() => {
    if (!ready || !progress || busy) return
    const what = autoStart(progress, pageTour)
    if (!what) return
    const key = `${what}@${pathname}`
    if (tried.current.has(key)) return
    // Wait a beat so the page's own content (and its data) is on screen to point at.
    const t = window.setTimeout(() => {
      tried.current.add(key)
      if (what === 'welcome') setWelcome(true)
      else begin(what)
    }, 700)
    return () => window.clearTimeout(t)
  }, [ready, progress, busy, pageTour, pathname, begin])

  const answer = useCallback((id: TourId, status: 'completed' | 'skipped') => {
    setProgress((p) => ({ ...p, [id]: status }))
    void save(id, status)
  }, [])

  const api = useMemo<TourApi>(
    () => ({
      pageTour,
      tourThisPage: () => {
        if (pageTour) begin(pageTour)
      },
      replay: async () => {
        try {
          await call('/tour/reset', { method: 'POST' })
        } catch {
          return
        }
        tried.current.clear()
        setProgress({ welcome: 'completed' })
        await save('welcome', 'completed')
        if (pathname === '/dashboard') begin('home')
        else router.push('/dashboard')
      },
    }),
    [pageTour, begin, pathname, router],
  )

  return (
    <TourContext.Provider value={api}>
      {children}
      {welcome && (
        <WelcomeTour
          name={agentName}
          onTake={() => {
            setWelcome(false)
            answer('welcome', 'completed')
            if (pageTour) begin(pageTour)
          }}
          onSkip={() => {
            setWelcome(false)
            answer('welcome', 'skipped')
          }}
        />
      )}
      {run && (
        <TourOverlay
          steps={run.steps}
          onFinish={() => {
            answer(run.id, 'completed')
            setRun(null)
          }}
          onSkip={() => {
            answer(run.id, 'skipped')
            setRun(null)
          }}
        />
      )}
    </TourContext.Provider>
  )
}
