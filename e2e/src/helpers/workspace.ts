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

// Paused if the platform switch (the console's Pause) or this Ava's own switch is on (ADR 0009 T3).
export async function isPaused(): Promise<boolean> {
  const rows = await query<{ value: { on?: boolean } }>(
    "SELECT value FROM platform_state WHERE key = 'frozen' UNION ALL SELECT value FROM agent_state WHERE key = 'frozen'",
  )
  return rows.some((r) => Boolean(r.value?.on))
}

// --- the workspace ------------------------------------------------------------------------

export const OFFLINE_REPLY = /Offline mode: no Claude API key yet, so I can only echo you\./

// A line from the introduction (fixtures.completeIntroduction), so its presence
// means the conversation history has loaded.
export const INTRO_LINE = 'I lead applied AI at PebbleRoad in Singapore.'

// Open the dashboard's chat and wait until the conversation history has loaded.
// (The old /workspace screen became the dashboard; /workspace redirects to /dashboard/chat.)
export async function openChat(page: Page): Promise<void> {
  await page.goto('/dashboard/chat')
  await expect(page).toHaveURL(/\/dashboard\/chat$/)
  await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
}

// The dashboard's notebook page, with its list loaded.
export async function openNotebookPage(page: Page): Promise<void> {
  await page.goto('/dashboard/notebook')
  await expect(page.getByRole('heading', { level: 1, name: "Ava's notebook" })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Forget this' }).first()).toBeVisible()
}

// The dashboard's Connections page.
export async function openConnections(page: Page): Promise<void> {
  await page.goto('/dashboard/connections')
  await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()
}

// The dashboard's Home greeting depends on the time of day.
export function homeHeading(page: Page, person: Person) {
  const first = person.name.split(' ')[0]
  return page.getByRole('heading', { level: 1, name: new RegExp(`^Good (morning|afternoon|evening), ${first}$`) })
}

// The chat page's greeting.
export function chatHeading(page: Page, person: Person) {
  return page.getByRole('heading', { level: 1, name: `Hello, ${person.name.split(' ')[0]}. What shall we work on?` })
}

// On a phone the sidebar is a sheet behind the "Toggle Sidebar" button.
export function sidebarTrigger(page: Page) {
  // The trigger in the page's top bar (the sidebar's edge "rail" has the same name).
  return page.getByRole('main').getByRole('button', { name: 'Toggle Sidebar' })
}

export async function openSidebarIfHidden(page: Page): Promise<void> {
  const link = page.getByRole('link', { name: 'Notebook', exact: true })
  await expect(sidebarTrigger(page)).toBeVisible()
  if (await link.isVisible()) return
  await sidebarTrigger(page).click()
  await expect(link).toBeVisible()
}

// A page in the dashboard sidebar (Home, Chat, Notebook, People, Connections, Settings).
export async function goToDashboardPage(page: Page, name: string): Promise<void> {
  await openSidebarIfHidden(page)
  await page.getByRole('link', { name, exact: true }).click()
}

// Log out from the dashboard: the account button at the foot of the sidebar opens a
// menu with "Log out".
export async function logOutFromSidebar(page: Page, person: Person): Promise<void> {
  await openSidebarIfHidden(page)
  await page.getByRole('button', { name: `Account: signed in as ${person.name}` }).click()
  await page.getByRole('menuitem', { name: 'Log out' }).click()
  await expect(page).not.toHaveURL(/\/dashboard/)
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
