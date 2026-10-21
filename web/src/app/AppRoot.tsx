import '@fontsource-variable/geist'
import './app.css'
import { MotionConfig } from 'motion/react'
import { useCallback, useEffect, useState } from 'react'
import { call, getSession, setSession, type Me } from './api'
import { Hire } from './Hire'
import { Marketplace } from './Marketplace'
import { Meet } from './Meet'
import { Profile } from './Profile'
import { go, useRoute } from './router'
import { Workspace } from './Workspace'

export type Session = {
  me: Me | null
  loading: boolean
  refresh: () => Promise<Me | null>
  signOut: () => Promise<void>
  signedIn: (token: string) => Promise<Me | null>
}

function useSession(): Session {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(() => Boolean(getSession()))

  const refresh = useCallback(async () => {
    if (!getSession()) {
      setMe(null)
      setLoading(false)
      return null
    }
    try {
      const m = await call<Me>('/me')
      setMe(m)
      return m
    } catch {
      setMe(null)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signOut = useCallback(async () => {
    try {
      await call('/signout', { method: 'POST' })
    } catch {
      // Signing out locally is what matters.
    }
    setSession(null)
    setMe(null)
    go('/')
  }, [])

  const signedIn = useCallback(
    async (token: string) => {
      setSession(token)
      return refresh()
    },
    [refresh],
  )

  return { me, loading, refresh, signOut, signedIn }
}

export default function AppRoot() {
  const route = useRoute()
  const session = useSession()
  const [first, second] = route.path
  const { signedIn } = session

  // The owner-link command opens #/signin?session=... on this computer.
  useEffect(() => {
    if (first !== 'signin') return
    const token = route.query.get('session')
    if (!token) {
      go('/', true)
      return
    }
    void signedIn(token).then((m) => go(m ? (m.onboarding.done ? '/workspace' : '/meet') : '/', true))
  }, [first, route.query, signedIn])

  let page
  if (first === 'signin' || session.loading) page = <div className="min-h-dvh mesh" aria-busy="true" />
  else if (first === 'agents' && second) page = <Profile slug={second} session={session} />
  else if (first === 'hire' && second) page = <Hire slug={second} session={session} />
  else if (first === 'meet') page = <Meet session={session} />
  else if (first === 'workspace') page = <Workspace session={session} />
  else page = <Marketplace session={session} section={route.query.get('s')} />
  return <MotionConfig reducedMotion="user">{page}</MotionConfig>
}
