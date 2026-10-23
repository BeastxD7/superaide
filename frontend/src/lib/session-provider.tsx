'use client'

import { useRouter } from 'next/navigation'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { auth, call, isUnauthorized, setSignedOutHandler, type Account, type Me } from '@/lib/api'
import { PWA_ENABLED } from '@/lib/flags'
import { SessionContext } from '@/lib/session-context'
import { needsAccount } from '@/lib/session'

// How often a visible tab re-reads the agent's state (see `sync`).
const SYNC_MS = 15_000

type ProviderProps = { initialAccount: Account | null; hasSession: boolean; children: ReactNode }

// The signed-in account comes from the server render, so every page knows at
// once who is signed in (no logged-out flash). The hired agent's workspace state
// is loaded in the browser. With no session cookie there is nothing to check.
//
// "Couldn't load" is kept apart from "not hired" and from "signed out": a failed read keeps what
// was known and sets loadFailed, so screens can offer a retry instead of redirecting. Only a 401
// means the session ended (here, and in lib/api.ts for every /app call).
export function SessionProvider({ initialAccount, hasSession, children }: ProviderProps) {
  const router = useRouter()
  const [account, setAccount] = useState<Account | null>(initialAccount)
  const [me, setMe] = useState<Me | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  // Signed out (no cookie, no account): the server already checked, so nothing is loading.
  const [loading, setLoading] = useState(hasSession || initialAccount !== null)
  // The latest values, for async code that must not use a stale closure.
  const latest = useRef<{ account: Account | null; me: Me | null }>({ account: initialAccount, me: null })

  // Set the account, the agent state and the failure flag together, so no page ever renders
  // a half-updated session (for example the new account with the old agent state).
  const commit = useCallback((acc: Account | null, m: Me | null, failed: boolean) => {
    latest.current = { account: acc, me: m }
    setAccount(acc)
    setMe(m)
    setLoadFailed(failed)
    setLoading(false)
  }, [])

  // The agent's state for an account. Not hired: null. Failed: keep what we had and say it failed.
  const fetchAgent = useCallback(async (acc: Account | null, previous: Me | null) => {
    if (!acc?.hire) return { account: acc, me: null, failed: false }
    try {
      return { account: acc, me: await call<Me>('/me'), failed: false }
    } catch (e) {
      if (isUnauthorized(e)) return { account: null, me: null, failed: false }
      return { account: acc, me: previous, failed: true }
    }
  }, [])

  // Re-read both the account and the agent state from the server.
  const refresh = useCallback(async () => {
    let acc: Account | null
    let failed = false
    try {
      acc = await auth.me()
    } catch (e) {
      if (isUnauthorized(e)) acc = null
      else {
        // Can't tell: keep who we thought was signed in rather than treating this as signed out.
        acc = latest.current.account
        failed = true
      }
    }
    const next = await fetchAgent(acc, latest.current.me)
    commit(next.account, next.me, failed || next.failed)
    return next.me
  }, [fetchAgent, commit])

  useEffect(() => {
    if (hasSession || initialAccount) void refresh()
  }, [refresh, hasSession, initialAccount])

  // One shared source for "what is Ava's state": every surface (header, sidebar, Home, Chat) reads `me`, so it is re-read
  // whenever something outside this tab may have changed it (the operator pausing Ava, another tab, a channel linked in
  // Telegram) and not only after this tab's own actions: when the tab regains focus or becomes visible, and on a light poll while
  // it is visible. A quiet read: nothing re-renders unless the state really changed, and a failed read changes nothing.
  const sync = useCallback(async () => {
    const known = latest.current.account
    if (!known) return
    try {
      // Who owns the browser's one session cookie now? Another tab may have signed in as someone else (or out): this tab must not
      // keep showing the old person's name over the new person's data.
      const who = await auth.me()
      if (who.user.id !== known.user.id) {
        await refresh()
        router.refresh()
        return
      }
      if (!who.hire) return
      const fresh = await call<Me>('/me')
      if (JSON.stringify(fresh) !== JSON.stringify(latest.current.me)) commit(latest.current.account, fresh, false)
    } catch (e) {
      if (isUnauthorized(e)) void refresh() // the session ended: the same path as any other 401
    }
  }, [commit, refresh, router])

  // A sign-in or sign-out in another tab tells this one at once (BroadcastChannel); focus and the poll catch the rest.
  const channel = useRef<BroadcastChannel | null>(null)
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return
    const bc = new BroadcastChannel('superaide-session')
    channel.current = bc
    bc.onmessage = () => void sync()
    return () => {
      bc.close()
      channel.current = null
    }
  }, [sync])
  useEffect(() => {
    if (!hasSession && !initialAccount) return
    const onVisible = () => {
      if (document.visibilityState === 'visible') void sync()
    }
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    const timer = window.setInterval(onVisible, SYNC_MS)
    return () => {
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
      window.clearInterval(timer)
    }
  }, [sync, hasSession, initialAccount])

  // The session ended while a page was open (a 401 from any /app call): forget the account, and on
  // a page that needs one go to log in, coming back here afterwards.
  useEffect(
    () =>
      setSignedOutHandler(() => {
        if (!latest.current.account) return
        commit(null, null, false)
        const path = window.location.pathname
        if (needsAccount(path)) router.replace(`/login?next=${encodeURIComponent(path)}`)
      }),
    [commit, router],
  )

  const signedIn = useCallback(
    async (acc: Account) => {
      // Load the agent state before announcing the account, so no page sees
      // "signed in, hired, but no agent yet" and bounces to the home page.
      const next = await fetchAgent(acc, null)
      commit(next.account, next.me, next.failed)
      router.refresh() // re-render server components with the new session cookie
      channel.current?.postMessage('signed-in') // other tabs of this browser re-check who they belong to
    },
    [fetchAgent, commit, router],
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
    channel.current?.postMessage('signed-out')
    window.location.assign('/')
  }, [])

  const value = useMemo(
    () => ({ account, me, loading, loadFailed, refresh, signedIn, logOut }),
    [account, me, loading, loadFailed, refresh, signedIn, logOut],
  )
  // Page state (open forms, loaded lists) belongs to one person: a different account remounts it all.
  return <SessionContext.Provider value={value}><Fragment key={account?.user.id ?? 'signed-out'}>{children}</Fragment></SessionContext.Provider>
}
