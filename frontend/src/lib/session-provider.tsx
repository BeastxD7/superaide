'use client'

import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { auth, call, type Account, type Me } from '@/lib/api'
import { PWA_ENABLED } from '@/lib/flags'
import { SessionContext } from '@/lib/session-context'

type ProviderProps = { initialAccount: Account | null; hasSession: boolean; children: ReactNode }

// The signed-in account comes from the server render, so every page knows at
// once who is signed in (no logged-out flash). The hired agent's workspace state
// is loaded in the browser. With no session cookie there is nothing to check.
export function SessionProvider({ initialAccount, hasSession, children }: ProviderProps) {
  const router = useRouter()
  const [account, setAccount] = useState<Account | null>(initialAccount)
  const [me, setMe] = useState<Me | null>(null)
  // Signed out (no cookie, no account): the server already checked, so nothing is loading.
  const [loading, setLoading] = useState(hasSession || initialAccount !== null)

  const loadAgent = useCallback(async (acc: Account | null) => {
    if (!acc?.hire) {
      setMe(null)
      return null
    }
    try {
      const m = await call<Me>('/me')
      setMe(m)
      return m
    } catch {
      setMe(null)
      return null
    }
  }, [])

  // Re-read both the account and the agent state from the server.
  // State is set only once the server answers (written with .then so the effect lint can see that).
  const refresh = useCallback(
    () =>
      auth
        .me()
        .catch(() => null)
        .then(async (acc: Account | null) => {
          setAccount(acc)
          const m = await loadAgent(acc)
          setLoading(false)
          return m
        }),
    [loadAgent],
  )

  useEffect(() => {
    if (hasSession || initialAccount) void refresh()
  }, [refresh, hasSession, initialAccount])

  const signedIn = useCallback(
    async (acc: Account) => {
      // Load the agent state before announcing the account, so no page sees
      // "signed in, hired, but no agent yet" and bounces to the home page.
      await loadAgent(acc)
      setAccount(acc)
      router.refresh() // re-render server components with the new session cookie
    },
    [loadAgent, router],
  )

  // A full page load to the home page: nothing from the signed-in session stays
  // in memory, and pages that need an account don't race to redirect elsewhere.
  const logOut = useCallback(async () => {
    if (PWA_ENABLED) {
      // This device stops getting the owner's pushes (ADR 0011 section 3.6).
      const { dropSubscription } = await import('@/features/pwa/push')
      await dropSubscription().catch(() => undefined)
    }
    try {
      await auth.logout()
    } catch {
      // Already signed out on the server; leave the page either way.
    }
    window.location.assign('/')
  }, [])

  const value = useMemo(
    () => ({ account, me, loading, refresh, signedIn, logOut }),
    [account, me, loading, refresh, signedIn, logOut],
  )
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
