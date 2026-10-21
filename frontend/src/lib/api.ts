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
  hired?: boolean
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

export type SourceRef = { n: number; uri: string; kind: 'memory' | 'message' | 'web' | 'other'; title: string; cited_text: string }
export type Message = {
  id: string
  from: 'you' | 'agent'
  text: string
  at: string
  kind?: 'onboarding' | 'chat'
  sources?: SourceRef[]
  activity?: WorkStep[]
  reasoning?: string
}

// One thing Ava did while answering: a tool or skill call, or a line where she says what she's doing.
export type WorkStep = {
  kind: 'tool' | 'say'
  id?: string
  name?: string
  state?: 'running' | 'done' | 'error'
  label: string
  detail?: string
}

// Events from POST /app/chat/stream (Server-Sent Events).
export type ChatEvent =
  | { event: 'status'; data: { text: string } }
  | { event: 'reasoning'; data: { delta: string } }
  | { event: 'text'; data: { delta: string } }
  | { event: 'narration'; data: { text: string } }
  | { event: 'retract'; data: Record<string, never> }
  | { event: 'tool'; data: { id: string; name: string; state: 'running' | 'done' | 'error'; label: string; detail: string } }
  | { event: 'done'; data: { mode: string; reply: string | null; notes: string[] } }
  | { event: 'error'; data: { message: string } }
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

export type PersonCard = {
  id: string
  name: string
  relationship: string
  vip: boolean
  channels: string[]
}

export type Preferences = {
  urgent_topics: string[]
  digest: string
  interrupt_channel: string
  quiet_start: string
  quiet_end: string
  vip_breaks_quiet: boolean
  timezone: string
  shareable: { value: string; label: string }[]
  never_share: string
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

// Send a chat message and get Ava's progress as it happens. Uses fetch, since axios can't
// read a streamed body in the browser; the same-origin /api proxy passes the stream through.
export async function streamChat(text: string, onEvent: (e: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${API_URL}/app/chat/stream`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'superaide', Accept: 'text/event-stream' },
      body: JSON.stringify({ text }),
      signal,
    })
  } catch {
    throw new ApiError(0, "Can't reach Superaide. Check your connection, or that start.command is still running.")
  }
  if (!res.ok || !res.body) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
    const detail = body?.detail
    const message =
      typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail ? String((detail as { message: unknown }).message) : `Request failed (${res.status})`
    throw new ApiError(res.status, message)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let cut: number
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join('\n')) } as ChatEvent)
    }
  }
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
  changePassword: (current: string, next: string) =>
    request<{ status: string }>('/auth/password', { method: 'POST', json: { current, new: next } }),
  logoutEverywhere: () => request<{ status: string }>('/auth/logout-everywhere', { method: 'POST' }),
}

export type ApprovalItem = {
  id: string
  kind: string
  summary: string
  requested_by: string | null
  status: 'pending' | 'approved' | 'rejected'
  result: string | null
  at: string
  decided_at: string | null
}

export type Reminder = {
  id: string
  kind: 'reminder' | 'follow_up'
  text: string
  due_at: string
  due_text: string
  status: 'open' | 'done' | 'cancelled'
  delivered_at: string | null
  source: 'chat' | 'dashboard'
}

export type SkillCard = {
  id: string
  title: string
  summary: string
  publisher: string
  tier: 'official' | 'community'
  version: string
  installed: boolean
  removable: boolean
  permissions: { id: string; label: string }[]
  needs_consent: boolean
  tools: string[]
  cost: string | null
}
