'use client'

import { useEffect, useState } from 'react'
import { AppError, call, type RehearsalSession } from '@/lib/api'

// Whether the rehearsal room is turned on. The backend answers 404 while it is off, so callers render
// nothing and the app looks exactly as it did before. A failed request (offline, restart) counts as off
// for now and does not stick.
export function useRehearsalEnabled(): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => {
    let live = true
    call<{ sessions: RehearsalSession[] }>('/rehearsal').then(
      () => live && setOn(true),
      (e: unknown) => {
        if (live && !(e instanceof AppError && e.status === 404)) setOn(false)
      },
    )
    return () => {
      live = false
    }
  }, [])
  return on
}
