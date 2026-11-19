'use client'

import { createContext, useContext } from 'react'
import type { PageTourId } from '@/features/tour/tours'

export type TourApi = {
  /** The tour of the page being shown, or null when it has none. */
  pageTour: PageTourId | null
  /** Run this page's tour now, whatever was answered before (the account menu). */
  tourThisPage: () => void
  /** Forget every answer and start over from Home (Settings). */
  replay: () => Promise<void>
}

export const TourContext = createContext<TourApi | null>(null)

export function useTour(): TourApi {
  const t = useContext(TourContext)
  if (!t) throw new Error('useTour must be used inside <TourProvider>')
  return t
}
