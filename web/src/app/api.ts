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

// The signed-in account, from /auth/me. The session itself is an httpOnly cookie
// the browser sends automatically; page code never sees it.
export type Account = {
  user: { id: string; email: string; name: string; timezone: string; created_at: string }
  hire: { agent: string; since: string; onboarding_done: boolean } | null
}

export { ApiError as AppError }

// Axios client for the backend, sending the session cookie with each request.
const client = createClient(API_URL, { withCredentials: true })

type Init = { method?: 'GET' | 'POST'; json?: unknown }

async function request<T>(url: string, init?: Init): Promise<T> {
  const res = await client.request<T>({ url, method: init?.method ?? 'GET', data: init?.json })
  return res.data
}

// The app's endpoints (/app/...).
export function call<T>(path: string, init?: Init): Promise<T> {
  return request<T>(`/app${path}`, init)
}

// Accounts (/auth/...).
export const auth = {
  me: () => request<Account>('/auth/me'),
  signup: (name: string, email: string, password: string) =>
    request<Account>('/auth/signup', { method: 'POST', json: { name, email, password } }),
  login: (email: string, password: string) => request<Account>('/auth/login', { method: 'POST', json: { email, password } }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
}
