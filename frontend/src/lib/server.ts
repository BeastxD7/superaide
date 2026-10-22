// Server-side calls to the backend API, used while rendering pages on the server.
// The browser never calls these URLs directly; it goes through /api (see app/api/[...path]/route.ts).
import { cookies } from 'next/headers'
import type { Account, AgentCard } from '@/lib/api'

export const API_INTERNAL_URL = (process.env.API_INTERNAL_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')
export const SESSION_COOKIE = process.env.SESSION_COOKIE_NAME ?? 'superaide_session'

async function get<T>(path: string, cookie?: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_INTERNAL_URL}${path}`, {
      headers: cookie ? { cookie } : undefined,
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    })
    return res.ok ? ((await res.json()) as T) : null
  } catch {
    return null // the API is down or slow: render the page and let the browser retry
  }
}

// The signed-in account for this request. `hasSession` says whether the browser
// sent a session cookie at all (the account can still be null if it expired).
export async function currentSession(): Promise<{ account: Account | null; hasSession: boolean }> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value
  if (!token) return { account: null, hasSession: false }
  return { account: await get<Account>('/auth/me', `${SESSION_COOKIE}=${token}`), hasSession: true }
}

export async function agentCatalog(): Promise<AgentCard[] | null> {
  return get<AgentCard[]>('/app/agents')
}

// The public page of an owner's "Ask my Ava" link. null for an unknown, unpublished or revoked handle
// (the API answers each with the same 404), and while the feature is off.
export type AskPage = {
  handle: string
  display: string
  first_name: string | null
  notice: string
  token: string
  limits: { name: number; contact: number; topic: number; why: number }
}

export async function askPage(handle: string): Promise<AskPage | null> {
  return get<AskPage>(`/p/ask/${encodeURIComponent(handle)}`)
}
