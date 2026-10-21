// Helpers for the workspace, connections and operator console specs.
import { expect, type APIRequestContext, type APIResponse, type Browser, type BrowserContext, type Page } from '@playwright/test'
import { Api, type Person } from '../fixtures'
import { query } from '../db'
import { ADMIN_TOKEN, BASE_URL } from '../env'

// --- other browsers ---------------------------------------------------------------

export type Visitor = { context: BrowserContext; page: Page; api: Api }

// A separate browser (its own cookies and storage), on the same app.
export async function newVisitor(browser: Browser): Promise<Visitor> {
  const context = await browser.newContext({ baseURL: BASE_URL })
  const page = await context.newPage()
  return { context, page, api: new Api(context.request) }
}

// The same person, signed in from a second browser.
export async function signedInElsewhere(browser: Browser, person: Person): Promise<Visitor> {
  const v = await newVisitor(browser)
  const r = await v.api.login(person.email, person.password)
  expect(r.ok(), `log in as ${person.email}: ${r.status()} ${await r.text()}`).toBeTruthy()
  return v
}

// --- outside services ---------------------------------------------------------------

const OUTSIDE_HOSTS = ['anthropic.com', 'telegram.org', 't.me', 'slack.com', 'claude.com']

function isOutside(url: URL): boolean {
  return OUTSIDE_HOSTS.some((h) => url.hostname === h || url.hostname.endsWith(`.${h}`))
}

// Any request the browser tries to send to Claude, Telegram or Slack is stopped
// and recorded, so a test can assert that nothing left.
export async function recordOutsideCalls(target: Page | BrowserContext): Promise<string[]> {
  const attempted: string[] = []
  await target.route(isOutside, async (route) => {
    attempted.push(route.request().url())
    await route.abort('blockedbyclient')
  })
  return attempted
}

// --- the operator's admin API (through the app's /api proxy) ----------------------------

export class Admin {
  constructor(
    private readonly request: APIRequestContext,
    private readonly token: string | null = ADMIN_TOKEN,
  ) {}

  private headers(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {}
  }

  get(path: string): Promise<APIResponse> {
    return this.request.get(`/api/api${path}`, { headers: this.headers() })
  }

  post(path: string, data?: unknown): Promise<APIResponse> {
    return this.request.post(`/api/api${path}`, { data: data ?? {}, headers: this.headers() })
  }

  async pause(on: boolean): Promise<void> {
    const r = await this.post('/kill', { on })
    expect(r.ok(), `kill switch → ${r.status()} ${await r.text()}`).toBeTruthy()
  }
}

// --- what was stored --------------------------------------------------------------------

export type StoredEvent = { direction: 'in' | 'out'; content: string }

// The owner's in-app conversation, oldest first.
export async function webConversation(): Promise<StoredEvent[]> {
  return query<StoredEvent>(
    `SELECT e.direction, e.content FROM event e JOIN context c ON c.id = e.context_id
      WHERE c.provider = 'web' AND c.external_id LIKE 'web:%' ORDER BY e.created_at, e.id`,
  )
}

export async function eventsWithText(text: string): Promise<StoredEvent[]> {
  return query<StoredEvent>('SELECT direction, content FROM event WHERE content = $1 ORDER BY created_at', [text])
}

export type StoredMemory = { id: string; content: string; source: string; category: string; valid_to: Date | null }

export async function memories(): Promise<StoredMemory[]> {
  return query<StoredMemory>('SELECT id::text, content, source, category, valid_to FROM memory_item ORDER BY created_at')
}

export async function storedSecrets(): Promise<{ provider: string; name: string; hint: string }[]> {
  return query('SELECT provider, name, hint FROM integration_secret ORDER BY provider, name')
}

export async function auditActions(): Promise<string[]> {
  return (await query<{ action: string }>('SELECT action FROM audit_log ORDER BY created_at')).map((r) => r.action)
}

export async function isPaused(): Promise<boolean> {
  const rows = await query<{ value: { on?: boolean } }>("SELECT value FROM agent_state WHERE key = 'frozen'")
  return Boolean(rows[0]?.value?.on)
}

// --- the workspace ------------------------------------------------------------------------

export const OFFLINE_REPLY = /Offline mode: no Claude API key yet, so I can only echo you\./

// A line from the introduction (fixtures.completeIntroduction), so its presence
// means the conversation history has loaded.
export const INTRO_LINE = 'I lead applied AI at PebbleRoad in Singapore.'

// Open the workspace and wait until the conversation history has loaded. Sending
// before that replays the whole history with typing pauses (see the race test).
export async function openWorkspace(page: Page): Promise<void> {
  await page.goto('/workspace')
  await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
}

export function composer(page: Page, agent = 'Ava') {
  return page.getByRole('textbox', { name: `Message ${agent}` })
}

export function sendButton(page: Page) {
  return page.getByRole('button', { name: 'Send', exact: true })
}

// The notebook's list items (each fact is one).
export function notebookFact(page: Page, text: string | RegExp) {
  return page.getByRole('listitem').filter({ hasText: text })
}

// The page doesn't scroll sideways.
export async function expectNoSidewaysScroll(page: Page): Promise<void> {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: document.documentElement.clientWidth,
  }))
  expect(scroll, 'the page scrolls sideways').toBeLessThanOrEqual(width + 1)
}
