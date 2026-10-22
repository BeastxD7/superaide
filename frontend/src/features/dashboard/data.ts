'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call } from '@/lib/api'

// Load one /app endpoint for a dashboard page, with a way to reload it.
export function useAppData<T>(path: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)

  // State is set only once the request settles (written with .then so the effect lint can see that).
  const reload = useCallback(
    () =>
      call<T>(path).then(
        (d) => {
          setData(d)
          setError(null)
        },
        (e: unknown) => {
          setError(e instanceof AppError ? e.message : String(e))
        },
      ),
    [path],
  )

  useEffect(() => {
    void reload()
  }, [reload])

  return { data, error, reload }
}

export const CATEGORY_LABEL: Record<string, string> = {
  about: 'About you',
  work: 'Work',
  person: 'People',
  preference: 'Preference',
  urgency: 'Urgent',
  availability: 'Availability',
  sharing: 'Sharing',
  other: 'Note',
}

export const SOURCE_LABEL: Record<string, string> = {
  chat: 'from chat',
  intro_chat: 'from your introduction',
  intro_call: 'from our video call',
  document: 'from a file you shared',
  link: 'from a link you shared',
  telegram: 'from Telegram',
  slack: 'from Slack',
  owner_edit: 'added by you',
}

export const CHANNEL_LABEL: Record<string, string> = {
  web: 'In the app',
  telegram: 'Telegram',
  slack: 'Slack',
  app: 'In the app',
}

export const DIGEST_LABEL: Record<string, string> = {
  morning: 'Every morning',
  evening: 'Every evening',
  both: 'Morning and evening',
  none: 'No digest',
}

export function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s
}
