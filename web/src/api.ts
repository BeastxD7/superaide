// Talks to the agent's admin API (FastAPI on 127.0.0.1:8000 by default) through Axios.
import { API_URL, ApiError, createClient } from './http'

export type Gate = { label: string; status: 'met' | 'unmet' | 'later'; detail: string }

export type Overview = {
  agent_name: string
  stage: string
  next_stage: string
  frozen: boolean
  model_mode: string
  simulator: boolean
  channels: { telegram: boolean; slack: boolean }
  spend: Spend
  gates: Gate[]
  safety: {
    stranger_messages: number
    link_codes_sent: number
    links_made: number
    refused_actions: number
    rejected_webhooks: number
    refused_model_calls: number
  }
  counts: {
    people: number
    identities: number
    linked_identities: number
    conversations: number
    messages_24h: number
    memories: number
    pending_approvals: number
    refused_actions: number
  }
}

export type Spend = {
  cap_usd: number
  settled_usd: number
  reserved_usd: number
  remaining_usd: number
  calls: number
}

export type Identity = {
  id: string
  provider: string
  tenant_scope: string
  stable_key: string
  display_name: string
  trust_level: number
  method: string
  risk_flags: string[]
  last_seen_at: string | null
}

export type PersonRow = {
  id: string
  name: string
  relation: string
  status: string
  identities: Identity[]
  link_events: { action: string; method: string; actor: string; at: string }[]
}

export type Conversation = {
  id: string
  provider: string
  kind: string
  audience_class: string
  title: string
  messages: number
  last_at: string | null
}

export type EventRow = {
  id: string
  context_id: string
  direction: 'in' | 'out'
  sender: string
  trust: number
  audience_size: number
  audience_has_unknown: boolean
  content: string
  at: string
}

export type AuditRow = { id: string; actor: string; action: string; detail: Record<string, unknown>; at: string }

export type SpendEntry = {
  id: string
  purpose: string
  model: string
  status: string
  cost_usd: number
  reserved_usd: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  at: string
}

export type SimResult = {
  mode: string
  reply: string | null
  notes: string[]
  outbox: { text: string; stable_key?: string; to_context?: string }[]
}

const STORE_KEY = 'agi-agent-dashboard'

export type Connection = { base: string; token: string }

// The console talks to the backend API (port 8000 by default) like the rest of the frontend.
export function defaultBase(): string {
  return API_URL
}

export function loadConnection(): Connection | null {
  // start.command prints a link ending #/console?token=... so you are connected at once.
  const hash = window.location.hash
  const query = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : hash.slice(1)
  const fromHash = new URLSearchParams(query).get('token')
  if (fromHash) {
    history.replaceState(null, '', `${window.location.pathname}#/console`)
    const conn = { base: defaultBase(), token: fromHash }
    saveConnection(conn)
    return conn
  }
  try {
    const raw = localStorage.getItem(STORE_KEY)
    return raw ? (JSON.parse(raw) as Connection) : null
  } catch {
    return null
  }
}

export function saveConnection(c: Connection | null): void {
  try {
    if (c) localStorage.setItem(STORE_KEY, JSON.stringify(c))
    else localStorage.removeItem(STORE_KEY)
  } catch {
    // Storage can be unavailable (private mode); the session still works.
  }
}

export { ApiError }

// Same Axios client as the app, authorised with the operator's ADMIN_TOKEN.
export async function api<T>(conn: Connection, path: string, init?: { method?: 'GET' | 'POST'; body?: string }): Promise<T> {
  const client = createClient(conn.base.replace(/\/$/, ''), { token: () => conn.token })
  const res = await client.request<T>({ url: path, method: init?.method ?? 'GET', data: init?.body })
  return res.data
}
