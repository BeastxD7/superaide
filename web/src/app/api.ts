// The person-facing API under /app. Signed-in calls send the session token
// that /app/hire (or the owner-link command) gave this browser.

export type Hue = 'indigo' | 'teal' | 'amber'

export type AgentCard = {
  slug: string
  name: string
  role: string
  status: 'available' | 'coming_soon'
  hue: Hue
  stage: string
  price: string
  tagline: string
  skills: string[]
  never: string[]
  channels: string[]
  hired_by?: string | null
  notice?: string
}

export type Me = {
  person: { id: string; name: string }
  agent: { name: string; role: string; stage: string }
  onboarding: { step: string; done: boolean }
  brain: 'claude' | 'offline'
  paused: boolean
  channels: { web: boolean; telegram: boolean; telegram_available: boolean; slack: boolean }
}

export type StepInput =
  | { kind: 'chips'; options: string[] }
  | { kind: 'text'; placeholder: string; multiline: boolean }
  | { kind: 'multi'; options: string[]; other: boolean; submit: string }
  | { kind: 'voice'; placeholder: string; options: string[]; submit: string }
  | { kind: 'rules'; items: string[]; submit: string }
  | { kind: 'channels'; telegram_available: boolean; telegram_linked: boolean; invite_url: string | null }
  | { kind: 'topic'; options: string[]; placeholder: string; skip: string }
  | { kind: 'none' }

export type StepView = {
  step: string
  index: number
  total: number
  labels: string[]
  ava: string[]
  input: StepInput
  done: boolean
  hint: string | null
}

export type Message = { id: string; from: 'you' | 'agent'; text: string; at: string; kind?: 'onboarding' | 'chat' }
export type Memory = { id: string; text: string; private: boolean; at: string }

const SESSION_KEY = 'understudy.session'

export function base(): string {
  // Vite's dev server runs on 5173; the agent serves this page itself otherwise.
  return window.location.port === '5173' ? 'http://127.0.0.1:8000' : window.location.origin
}

export function getSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY)
  } catch {
    return memorySession
  }
}

let memorySession: string | null = null

export function setSession(token: string | null): void {
  memorySession = token
  try {
    if (token) localStorage.setItem(SESSION_KEY, token)
    else localStorage.removeItem(SESSION_KEY)
  } catch {
    // Private mode: the session lasts until the tab closes.
  }
}

export class AppError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function call<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const token = getSession()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(`${base()}/app${path}`, {
      ...init,
      body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
      headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
    })
  } catch {
    throw new AppError(0, "Can't reach the agent on this computer. Is start.command still running?")
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // keep the status text
    }
    if (res.status === 401) setSession(null)
    throw new AppError(res.status, detail)
  }
  return (await res.json()) as T
}
