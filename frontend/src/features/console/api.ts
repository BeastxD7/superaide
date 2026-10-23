// Talks to the agent's admin API (FastAPI on 127.0.0.1:8000 by default) through Axios.
import { API_URL, ApiError, createClient } from '@/lib/http'

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
  messages_more?: boolean
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

const STORE_KEY = 'superaide-console-v2'

// `access` is break-glass (ADR 0009): a reason and an instance, needed to see an owner's content.
// It lives only in this tab's memory, never in storage, and every use is recorded in the owner's log.
export type Access = { reason: string; instance: string }
export type Connection = { base: string; token: string; access?: Access }

export type InstanceRow = {
  id: string
  agent: string
  status: string
  since: string | null
  region: string
  key_source: string
  frozen: boolean
  channels: string[]
  counts: { people: number; conversations: number; messages_24h: number; memories: number }
  spend_usd: number
}

// The console calls the backend through the app's /api proxy, like the rest of the frontend.
export function defaultBase(): string {
  return API_URL
}

export function loadConnection(): Connection | null {
  // start.command prints a link ending /console?token=... so you are connected at once.
  const fromUrl = new URLSearchParams(window.location.search).get('token')
  if (fromUrl) {
    history.replaceState(null, '', window.location.pathname) // keep the token out of history
    const conn = { base: defaultBase(), token: fromUrl }
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
    if (c) localStorage.setItem(STORE_KEY, JSON.stringify({ base: c.base, token: c.token }))
    else localStorage.removeItem(STORE_KEY)
  } catch {
    // Storage can be unavailable (private mode); the session still works.
  }
}

export { ApiError }

// Same Axios client as the app, authorised with the operator's ADMIN_TOKEN.
export async function api<T>(
  conn: Connection,
  path: string,
  init?: { method?: 'GET' | 'POST'; body?: string; signal?: AbortSignal },
): Promise<T> {
  return (await apiFull<T>(conn, path, init)).data
}

async function apiFull<T>(conn: Connection, path: string, init?: { method?: 'GET' | 'POST'; body?: string; signal?: AbortSignal }) {
  const client = createClient(conn.base.replace(/\/$/, ''), { token: () => conn.token })
  const headers: Record<string, string> = {}
  if (conn.access) {
    // A header can only carry Latin-1: a curly quote or a dash would be dropped or refused. Send it
    // percent-encoded and say so; the API decodes it before recording it in the owner's log.
    headers['X-Operator-Reason'] = encodeURIComponent(conn.access.reason)
    headers['X-Operator-Reason-Encoding'] = 'uri'
    if (conn.access.instance) headers['X-Operator-Instance'] = conn.access.instance
  }
  return client.request<T>({ url: path, method: init?.method ?? 'GET', data: init?.body, headers, signal: init?.signal })
}

// Fetchers for usePaginated (ADR 0014), carrying the operator's token and break-glass reason like every console call.
// A paged route answers the standard `{items, next_cursor, has_more}`.
export type PageBody<T> = { items: T[]; next_cursor: string | null; has_more: boolean; total_estimate?: number | null }

export const pageFetcher =
  <T,>(conn: Connection) =>
  (path: string, signal: AbortSignal): Promise<PageBody<T>> =>
    api<PageBody<T>>(conn, path, { signal })

// `/api/events` keeps its bare array and pages by header (`X-Next-Cursor`, `X-Has-More`): wrap it as a page.
export const arrayPageFetcher =
  <T,>(conn: Connection) =>
  async (path: string, signal: AbortSignal): Promise<PageBody<T>> => {
    const res = await apiFull<T[]>(conn, path, { signal })
    return {
      items: res.data,
      next_cursor: (res.headers['x-next-cursor'] as string | undefined) ?? null,
      has_more: res.headers['x-has-more'] === 'true',
    }
  }
