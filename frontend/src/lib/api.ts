// The person-facing API under /app on the backend. Signed-in calls send the
// session token that /app/hire (or the owner-link command) gave this browser.
import { API_URL, ApiError, createClient, errorFromDetail } from '@/lib/http'

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
  channels: {
    web: boolean
    telegram: boolean
    telegram_available: boolean
    slack: boolean
    // Whether Telegram and Slack still accept the saved token. Optional: an older API doesn't send it.
    health?: Partial<Record<'telegram' | 'slack', { status: 'ok' | 'auth_failed' | 'unreachable' }>>
  }
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
  | {
      // Choose Ava's brain: the first step for an owner with no AI yet.
      kind: 'brain'
      ready: boolean
      provider: string | null
      credits: {
        available: boolean
        opted_in: boolean
        model: string
        allowance: { daily_usd: number | null; monthly_usd: number | null }
        used: { today_usd: number; month_usd: number }
      } | null
      models_url: string
    }
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

// A document that failed to save facts is tried again here (POST /app/intro/documents/{id}/retry).
export const retryDocument = (id: string) => call<IngestResult>(`/intro/documents/${id}/retry`, { method: 'POST' })

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

export type SourceRef = { n: number; uri: string; kind: 'memory' | 'message' | 'web' | 'calendar' | 'email' | 'other'; title: string; cited_text: string }
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

export type ConflictCard = {
  id: string
  text: string
  category: string
  source: string
  document: string | null
  at: string
}

export type Conflict = { claim: ConflictCard; current: ConflictCard | null }

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
  user: {
    id: string
    email: string
    name: string
    timezone: string
    email_verified?: boolean
    has_password?: boolean
    google?: boolean
    created_at: string
  }
  hire: { agent: string; since: string; onboarding_done: boolean } | null
}

export { ApiError as AppError }

// Axios client for the backend, sending the session cookie with each request.
const client = createClient(API_URL, { withCredentials: true })

type Init = { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; json?: unknown }

// A 401 from /app/* means the session has ended (signed out elsewhere, expired, password changed).
// The session provider registers one handler that clears the signed-in state and sends the person
// to log in, so no page has to handle it. Wrong-password answers from /auth/* are not session
// endings and never reach it.
let onSignedOut: (() => void) | null = null

export function setSignedOutHandler(handler: () => void): () => void {
  onSignedOut = handler
  return () => {
    if (onSignedOut === handler) onSignedOut = null
  }
}

export function isUnauthorized(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401
}

function sessionEnded(e: unknown, url: string): void {
  if (url.startsWith('/app/') && isUnauthorized(e)) onSignedOut?.()
}

async function request<T>(url: string, init?: Init): Promise<T> {
  try {
    const res = await client.request<T>({ url, method: init?.method ?? 'GET', data: init?.json })
    return res.data
  } catch (e) {
    sessionEnded(e, url)
    throw e
  }
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
    const err = errorFromDetail(res.status, body?.detail, `Request failed (${res.status})`)
    sessionEnded(err, '/app/chat/stream')
    throw err
  }
  await readEvents(res.body, (event, data) => onEvent({ event, data } as ChatEvent))
}

// Read a Server-Sent Events body, calling onEvent for each event as it arrives.
export async function readEvents(body: NonNullable<Response['body']>, onEvent: (event: string, data: unknown) => void): Promise<void> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value.replace(/\r\n/g, '\n') // servers may end lines with CRLF
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
      if (data.length) onEvent(event, JSON.parse(data.join('\n')))
    }
  }
}

// POST to an /app endpoint that answers with Server-Sent Events (the voice call). `body` is JSON or
// a multipart form (an utterance's audio). Same-origin, through the /api proxy, with the CSRF header.
export async function streamApp(
  path: string,
  body: FormData | Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const form = body instanceof FormData
  let res: Response
  try {
    res = await fetch(`${API_URL}/app${path}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'X-Requested-With': 'superaide',
        Accept: 'text/event-stream',
        ...(form ? {} : { 'Content-Type': 'application/json' }),
      },
      body: form ? body : JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, "Can't reach Superaide. Check your connection, or that start.command is still running.")
  }
  if (!res.ok || !res.body) {
    const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null
    throw errorFromDetail(res.status, detail?.detail, `Request failed (${res.status})`)
  }
  await readEvents(res.body, (event, data) => onEvent(event, data as Record<string, unknown>))
}

// Upload a file to an /app endpoint (multipart form).
export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  try {
    const res = await client.post<T>(`/app${path}`, form, { headers: { 'Content-Type': 'multipart/form-data' } })
    return res.data
  } catch (e) {
    sessionEnded(e, `/app${path}`)
    throw e
  }
}

// Accounts (/auth/...).
export const auth = {
  me: () => request<Account>('/auth/me'),
  signup: (name: string, email: string, password: string) =>
    request<Account>('/auth/signup', { method: 'POST', json: { name, email, password, timezone: browserTimeZone() } }),
  login: (email: string, password: string) => request<Account>('/auth/login', { method: 'POST', json: { email, password } }),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
  // Continue with Google (only when the server has it on): start returns Google's URL to open.
  googleEnabled: () => request<{ enabled: boolean }>('/auth/google'),
  // `page`: where this started, so a cancelled or failed sign-in comes back to it with `next` kept.
  googleStart: (next: string | null, page: 'login' | 'signup') =>
    request<{ url: string }>('/auth/google/start', { method: 'POST', json: { next, page, timezone: browserTimeZone() } }),
  changePassword: (current: string, next: string) =>
    request<{ status: string }>('/auth/password', { method: 'POST', json: { current, new: next } }),
  changeEmail: (email: string, password: string) =>
    request<Account>('/auth/email', { method: 'POST', json: { email, password } }),
  logoutEverywhere: () => request<{ status: string }>('/auth/logout-everywhere', { method: 'POST' }),
  sendVerification: () => request<{ status: string }>('/auth/verify-email/send', { method: 'POST' }),
  verifyEmail: (token: string) => request<{ status: string }>('/auth/verify-email', { method: 'POST', json: { token } }),
  requestReset: (email: string) =>
    request<{ status: string }>('/auth/password-reset/request', { method: 'POST', json: { email } }),
  resetPassword: (token: string, password: string) =>
    request<{ status: string }>('/auth/password-reset', { method: 'POST', json: { token, password } }),
}

// The IANA time zone the browser is set to, e.g. "Asia/Kolkata". The server falls back to UTC.
export function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined
  } catch {
    return undefined
  }
}

export type ApprovalItem = {
  id: string
  kind: string
  summary: string
  requested_by: string | null
  status: 'pending' | 'approved' | 'rejected'
  result: string | null
  notes?: string[]
  at: string
  decided_at: string | null
  needs_reason?: boolean
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
  // Settings that give the skill more tools (Google Calendar's "actions"), each with its own consent.
  options?: SkillOption[]
  google_url?: string | null // after switching calendar actions on: Google's consent page to finish
}

export type SkillOption = {
  setting: string
  title: string
  description: string
  on: boolean
  permissions: { id: string; label: string }[]
  tools: string[]
  needs_google?: boolean
}

// /app/providers: which AI model Ava thinks with (ADR 0007). Keys never come back, only a hint.
export type ProviderPreset = {
  id: string
  label: string
  base_url: string
  key_required: boolean
  key_hint: string
  guide_url: string
  local: boolean
  note: string
  warnings: string[]
}

export type ConnectedProvider = {
  key: string // this endpoint's name: the preset, or e.g. "custom-2" for another of the same kind
  preset: string
  base_url: string
  enabled: boolean
  key_hint: string
  models: string[]
  checked_at: string | null
}

export type RoleName = 'chat' | 'triage' | 'deep'

export type ModelSettings = {
  presets: ProviderPreset[]
  claude: { connected: boolean; via: 'app' | 'env' | null; key_hint: string }
  providers: ConnectedProvider[]
  roles: Record<RoleName, { provider: string; model: string }>
  brain: { ready: boolean; provider: string | null; model: string }
  priced: Record<string, string[]>
  // Providers whose key (or Superaide's credits) the service refused; "Key rejected: reconnect" when any.
  key_status?: { rejected: string[]; message: string }
  web_search: { connected: boolean; via: 'app' | 'env' | null; key_hint: string; usd_per_call: number }
  // Superaide AI credits: the platform's own Claude key, offered only when the server turns it on.
  credits: {
    available: boolean
    opted_in: boolean
    model: string
    // null: no limit of that kind for this owner
    allowance: { daily_usd: number | null; monthly_usd: number | null }
    used: { today_usd: number; month_usd: number }
  }
}

export type ConnectionTest = { ok: boolean; message: string; models: string[] }

// Triage and escalation (ADR 0010). Every /needs-you and /triage route answers 404 while the
// backend's TRIAGE_ENABLED is off, and the app then hides the inbox.
export type TriageDecision = 'handle' | 'hold' | 'interrupt' | 'defer' | 'verify'
export type TriageVerb = 'ack' | 'reply' | 'call' | 'not_urgent' | 'snooze_30m' | 'snooze_2h' | 'done'
export type TriageFeedback = 'useful' | 'not_urgent' | 'should_have_told' | 'wrong_handle' | 'good_handle'

export type GmailStatus = {
  installed: boolean
  client_ready: boolean
  connected: boolean
  address: string
  drafts: boolean
  drafts_allowed: boolean
}

export type NeedsYouItem = {
  id: string
  source?: 'email' | 'message'
  subject?: string | null
  decision: TriageDecision
  reason: string
  sender: string
  quotes: string[]
  rule_hits: string[]
  stage: string
  maybe_urgent: boolean
  shadow_decision: string | null
  feedback: TriageFeedback | null
  created_at: string
  // Set when someone Ava doesn't know left it: their own words, unverified.
  taken: { name: string; contact: string; topic: string; message: string; deadline_text: string; claimed_urgency: string } | null
  escalation: {
    id: string
    state: 'deferred' | 'notifying' | 'acked' | 'resolved' | 'expired'
    step: number
    cycle: number
    next_at: string | null
    acked_at: string | null
    acked_via: string | null
    resolution: string | null
    deliveries: { step: number; cycle: number; provider: string; status: string; sent_at: string | null }[]
  } | null
}

export type NeedsYou = {
  needs_you: NeedsYouItem[]
  handled: NeedsYouItem[]
  held: NeedsYouItem[]
  counts: { needs_you: number; handled: number; held: number }
  stage: string
}

export type TriageSettings = {
  ladder: { provider: string; delay_s: number }[]
  expire_after_s: number
  max_per_hour: number
  max_per_day: number
  vip_ignores_budget: 'critical' | 'always' | 'never'
  theta_interrupt: number
  theta_handle: number
  silenced_people: string[]
  silenced_topics: string[]
  autonomy_grants: string[]
  away_until: string | null
  away_visibility: 'everyone' | 'colleagues' | 'nobody'
  busy_until: string | null
  tell_sender_seen: boolean
  phone_enabled: boolean
}

// The "Ask my Ava" link (owner side). Every route answers 404 while ASK_LINK_ENABLED is off.
export type AskLinkStats = { views: number; submissions: number; dropped: number; get_own_clicks: number }

export type AskLinkView = {
  link: null | {
    handle: string
    path: string
    published: boolean
    revoked: boolean
    show_first_name: boolean
    display: string
  }
  suggested: string
  first_name: string
  notice: string
  stats: AskLinkStats
}

export type AskRank = 'unverified' | 'urgent' | 'normal' | 'low'

export type AskRequestCard = {
  id: string
  name: string
  contact: string
  contact_kind: 'email' | 'phone'
  topic: string
  why: string
  deadline: string | null
  rank: AskRank
  rule_hits: string[]
  status: 'new' | 'replied' | 'dismissed' | 'spam'
  created_at: string
  reply: null | { approval_id: string; status: 'pending' | 'approved' | 'rejected'; text: string }
}

// Receptionist mode (ADR 0010 TR5): Ava talks with people she doesn't know, on the owner's behalf.
export type ReceptionistSettings = {
  enabled: boolean
  show_first_name: boolean
  public_profile: string
  per_hour: number
  per_day: number
  greeting: string
}

export type TakenMessageView = {
  id: string
  provider: string
  handle: string
  name: string
  contact: string
  topic: string
  message: string
  deadline_text: string
  claimed_urgency: string
  rank: 'unverified' | 'urgent' | 'normal' | 'low'
  source: 'model' | 'code'
  status: 'new' | 'done' | 'dismissed' | 'spam'
  created_at: string
}
// The rehearsal room (#58). Every /rehearsal route answers 404 while the backend's REHEARSAL_ENABLED is off.
export type RehearsalTurn = { who: 'owner' | 'other'; text: string }
export type RehearsalPoint = { heading: string; advice: string; quote: string }
export type RehearsalSession = {
  id: string
  role: string
  notes: string
  turns: RehearsalTurn[]
  debrief: { points: RehearsalPoint[]; dropped: number } | null
  kept: boolean
  ended: boolean
  expires_at: string | null
  created_at: string
  notice: string
}
export type RehearsalReply = { paused: boolean; message?: string; reply?: string; session: RehearsalSession }
