import '@fontsource-variable/geist'
import './app.css'
import { MotionConfig } from 'motion/react'
import { useCallback, useEffect, useState } from 'react'
import { auth, call, type Account, type Me } from './api'
import { AuthPage } from './Auth'
import { Hire } from './Hire'
import { Marketplace } from './Marketplace'
import { Meet } from './Meet'
import { Profile } from './Profile'
import { useRoute } from './router'
import { Workspace } from './Workspace'

export type Session = {
  account: Account | null // who is signed in (null when logged out)
  me: Me | null // the hired agent's workspace state, when this account hired one
  loading: boolean
  refresh: () => Promise<Me | null>
  signedIn: (account: Account) => Promise<void>
  logOut: () => Promise<void>
}

function useSession(): Session {
  const [account, setAccount] = useState<Account | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

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
  const refresh = useCallback(async () => {
    let acc: Account | null = null
    try {
      acc = await auth.me()
    } catch {
      acc = null
    }
    setAccount(acc)
    const m = await loadAgent(acc)
    setLoading(false)
    return m
  }, [loadAgent])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signedIn = useCallback(
    async (acc: Account) => {
      setAccount(acc)
      await loadAgent(acc)
    },
    [loadAgent],
  )

  const logOut = useCallback(async () => {
    try {
      await auth.logout()
    } catch {
      // Already signed out on the server; clear the page either way.
    }
    setAccount(null)
    setMe(null)
    window.location.hash = '/'
  }, [])

  return { account, me, loading, refresh, signedIn, logOut }
}

export default function AppRoot() {
  const route = useRoute()
  const session = useSession()
  const [first, second] = route.path

  let page
  if (session.loading) page = <div className="min-h-dvh mesh" aria-busy="true" />
  else if (first === 'signup' || first === 'login')
    page = <AuthPage mode={first} next={route.query.get('next')} session={session} />
  else if (first === 'agents' && second) page = <Profile slug={second} session={session} />
  else if (first === 'hire' && second) page = <Hire slug={second} session={session} />
  else if (first === 'meet') page = <Meet session={session} />
  else if (first === 'workspace') page = <Workspace session={session} />
  else page = <Marketplace session={session} section={route.query.get('s')} />
  return <MotionConfig reducedMotion="user">{page}</MotionConfig>
}
