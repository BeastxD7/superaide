// The person-facing API under /app on the backend. Signed-in calls send the
// session token that /app/hire (or the owner-link command) gave this browser.
import { API_URL, ApiError, createClient } from '../http'

export type Hue = 'indigo' | 'sky' | 'rose'

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

export { ApiError as AppError }

// Axios client for the app's endpoints on the backend (/app/...), signed in with this browser's session.
const client = createClient(`${API_URL}/app`, getSession)

export async function call<T>(path: string, init?: { method?: 'GET' | 'POST'; json?: unknown }): Promise<T> {
  try {
    const res = await client.request<T>({ url: path, method: init?.method ?? 'GET', data: init?.json })
    return res.data
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) setSession(null)
    throw e
  }
}
