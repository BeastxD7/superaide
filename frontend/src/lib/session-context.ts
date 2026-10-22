'use client'

import { createContext, useContext } from 'react'
import type { Account, Me } from '@/lib/api'

export type Session = {
  account: Account | null // who is signed in (null when logged out)
  me: Me | null // the hired agent's workspace state, when this account hired one
  loading: boolean // true until the first check with the server finishes
  // True when the last attempt to read the account or the agent's state failed for a reason other
  // than "signed out" (a network blip, a 5xx). It is different from "not hired" (me is null and
  // there is no error), so screens show a retry instead of sending the owner away.
  loadFailed: boolean
  refresh: () => Promise<Me | null>
  signedIn: (account: Account) => Promise<void>
  logOut: () => Promise<void>
}

// The provider is in session-provider.tsx (a component-only file, for fast refresh).
export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession must be used inside <SessionProvider>')
  return session
}
