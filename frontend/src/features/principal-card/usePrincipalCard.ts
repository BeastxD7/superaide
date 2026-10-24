'use client'

import { useCallback, useEffect, useState } from 'react'
import { AppError, call, type PrincipalCard, type PrincipalCardStats, type SentCard } from '@/lib/api'

// The card settings and the cards sent. The backend answers 404 while the card is off, so callers render
// nothing and the app looks exactly as it did before.
export function usePrincipalCard() {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [card, setCard] = useState<PrincipalCard | null>(null)
  const [sent, setSent] = useState<SentCard[]>([])
  const [stats, setStats] = useState<PrincipalCardStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(
    () =>
      Promise.all([
        call<PrincipalCard>('/principal-card'),
        call<{ cards: SentCard[] }>('/principal-card/sent'),
        call<PrincipalCardStats>('/principal-card/stats'),
      ]).then(
        ([c, s, st]) => {
          setCard(c)
          setSent(s.cards)
          setStats(st)
          setEnabled(true)
          setError(null)
        },
        (e: unknown) => {
          if (e instanceof AppError && e.status === 404) {
            setEnabled(false)
            return
          }
          setError(e instanceof AppError ? e.message : String(e))
        },
      ),
    [],
  )
  useEffect(() => {
    void reload()
  }, [reload])
  return { enabled, card, sent, stats, error, reload }
}

export function usePrincipalCardEnabled(): boolean {
  return usePrincipalCard().enabled === true
}
