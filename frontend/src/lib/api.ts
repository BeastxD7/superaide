// The person-facing API under /app on the backend. Signed-in calls send the
// session token that /app/hire (or the owner-link command) gave this browser.
import { API_URL, ApiError, createClient } from '@/lib/http'

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

export type SharedDoc = {
  id: string
  kind: string
  title: string
  source: string
  status: 'stored' | 'read' | 'failed'
  facts: number
  warnings: string[]
}

export type Option = { value: string; label: string }

export type StepInput =
  | { kind: 'chips'; options: string[] }
  | { kind: 'intro'; placeholder: string; documents: SharedDoc[]; facts: number; call_questions: number }
  | {
      kind: 'people'
      relationships: string[]
      existing: { name: string; relationship: string; vip: boolean }[]
      submit: string
      skip: string
    }
  | { kind: 'urgent'; options: string[]; selected: string[]; digests: Option[]; digest: string; submit: string }
  | {
      kind: 'reach'
      channels: (Option & { available: boolean; note: string })[]
      channel: string
      quiet_start: string
      quiet_end: string
      vip_breaks_quiet: boolean
      timezone: string
      submit: string
    }
  | {
      kind: 'share'
      options: Option[]
      selected: string[]
      never: string
      never_placeholder: string
      submit: string
      skip: string
    }
  | { kind: 'rules'; items: string[]; submit: string }
  | { kind: 'connect'; telegram_available: boolean; telegram_linked: boolean; invite_url: string | null; submit: string }
  | { kind: 'none' }

export type IngestResult = {
  document_id: string
  title: string
  facts: number
  duplicate: boolean
  status: string
  message: string
  warnings: string[]
}

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
export type Memory = {
  id: string
  text: string
  category: string
  source: string
  document: string | null
  shareable: boolean
  private: boolean
  at: string
}

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

// Upload a file to an /app endpoint (multipart form).
export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const res = await client.post<T>(`/app${path}`, form, { headers: { 'Content-Type': 'multipart/form-data' } })
  return res.data
}

// Accounts (/auth/...).
export const auth = {
  me: () => request<Account>('/auth/me'),
  signup: (name: string, email: string, password: string) =>
    request<Account>('/auth/signup', { method: 'POST', json: { name, email, password } }),
  login: (email: string, password: string) => request<Account>('/auth/login', { method: 'POST', json: { email, password } }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
}
