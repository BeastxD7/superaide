// QA bug hunt: the signed-in dashboard (/dashboard/*). Home, Notebook, People, Settings,
// Connections, the sidebar, deep links and reloads, and slow or failing API responses.
import { createCipheriv, randomBytes } from 'node:crypto'
import type { Page, Route } from '@playwright/test'
import { expect, query, test } from '../src/fixtures'
import { apiEnv } from '../src/env'
import {
  composer,
  goToDashboardPage,
  homeHeading,
  INTRO_LINE,
  openChat,
  openConnections,
  openNotebookPage,
  storedSecrets,
} from '../src/helpers/workspace'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/dashboard`

const serverError = (r: Route) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Something went wrong on our side."}' })

// The dashboard scrolls inside its own column, so a full-page shot needs a tall window.
async function shot(page: Page, file: string) {
  const size = page.viewportSize()
  await page.setViewportSize({ width: size?.width ?? 1280, height: 1800 })
  await page.screenshot({ path: `${QA}/${file}`, fullPage: true })
  if (size) await page.setViewportSize(size)
}

// Store a credential the way the vault does (copied from workspace.spec.ts, which doesn't export it).
async function plantSecret(provider: string, name: string, value: string): Promise<void> {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>(
    "SELECT tenant_id, agent_id FROM context WHERE external_id LIKE 'web:%' LIMIT 1",
  )
  const key = Buffer.from(apiEnv().SECRETS_MASTER_KEY, 'base64')
  const nonce = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key, nonce)
  cipher.setAAD(Buffer.from(`${scope.agent_id}/${provider}/${name}`))
  const ct = Buffer.concat([cipher.update(value, 'utf8'), cipher.final(), cipher.getAuthTag()])
  const token = Buffer.concat([Buffer.from([1]), nonce, ct]).toString('base64')
  const prefix = value.slice(0, 8).includes('-') ? `${value.split('-')[0]}-` : value.slice(0, 3)
  const hint = `${prefix}…${value.slice(-4)}`
  await query(
    `INSERT INTO integration_secret (id, tenant_id, agent_id, aad, provider, name, ciphertext, hint, meta, updated_at, created_at)
     VALUES (gen_random_uuid(), $1, $2, 'instance', $3, $4, $5, $6, '{}'::jsonb, now(), now())`,
    [scope.tenant_id, scope.agent_id, provider, name, token, hint],
  )
}

// Telegram as the Connections screen sees it once a saved bot token has been refused by Telegram.
// Planting a real Telegram token would make the API call api.telegram.org (GET /app/me runs getMe),
// so the browser's view of /app/connections and /app/me is rewritten instead.
async function telegramRejected(page: Page, opts: { ownerLinked: boolean }) {
  const since = '2026-10-21T09:30:00+00:00'
  await page.route('**/api/app/connections', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.services.telegram = {
      connected: true,
      via: 'app',
      hints: { bot_token: '123…wxyz' },
      meta: { username: 'qa_example_bot' },
      health: { status: 'auth_failed', last_error: 'Unauthorized', since },
    }
    await route.fulfill({ response: res, json: body })
  })
  await page.route('**/api/app/me', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.channels.telegram = opts.ownerLinked
    body.channels.telegram_available = true
    // What the API now sends for a refused token (channels/health.py), the same verdict Connections shows.
    body.channels.health = { telegram: { status: 'auth_failed', last_error: 'Unauthorized', since }, slack: { status: 'ok' } }
    await route.fulfill({ response: res, json: body })
  })
}

test.describe('dashboard: home', () => {
  test('home shows every card filled in when the API answers', async ({ page, owner }) => {
    await page.goto('/dashboard')
    await expect(homeHeading(page, owner)).toBeVisible()
    await expect(page.getByText('Priya', { exact: true })).toBeVisible()
    await expect(page.getByText(/^Quiet 22:00–07:00/)).toBeVisible()
    await expect(page.getByText(/^Latest: /)).toBeVisible()
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  })

  test('a card whose data cannot be loaded says so instead of loading forever', async ({ page, owner }) => {
    await page.route('**/api/app/notebook', serverError)
    await page.route('**/api/app/preferences', (r) => r.abort('connectionrefused'))
    await page.goto('/dashboard')
    await expect(homeHeading(page, owner)).toBeVisible()
    // The cards that did load are filled in…
    await expect(page.getByText('Priya', { exact: true })).toBeVisible()
    await shot(page, 'home-cards-spin-forever.png')
    // …and the two that failed say why, and stop "loading".
    const knows = page.locator('[data-slot="card"]').filter({ hasText: 'What Ava knows' })
    const rules = page.locator('[data-slot="card"]').filter({ hasText: 'Your rules' })
    await expect(knows.getByText(/couldn.t|can.t|went wrong|try again/i)).toBeVisible({ timeout: 5000 })
    await expect(rules.getByText(/couldn.t|can.t|went wrong|try again/i)).toBeVisible({ timeout: 5000 })
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  })

  test('"Latest" on the notebook card is the newest note', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await composer(page).fill('Remember that the board meets on Thursdays')
    await composer(page).press('Enter')
    await expect(page.getByText('Saved to your notebook. Only you can see it.')).toBeVisible()
    await goToDashboardPage(page, 'Home')
    await expect(page.getByText(/^Latest: .*board meets on Thursdays/i)).toBeVisible()
  })
})

test.describe('dashboard: session', () => {
  test('a reload while the agent API is briefly failing keeps the owner in the dashboard', async ({ page, owner }) => {
    test.fail(true, 'BUG: a failed GET /app/me sets me=null, and DashboardShell then router.replace("/") sends a hired, introduced owner to the marketplace')
    await page.goto('/dashboard/notebook')
    await expect(page.getByRole('heading', { level: 1, name: "Ava's notebook" })).toBeVisible()
    let failures = 0
    await page.route('**/api/app/me', (r) => {
      failures += 1
      return serverError(r)
    })
    await page.reload()
    // Wait until the app has asked for (and been refused) the agent state.
    await expect.poll(() => failures).toBeGreaterThan(0)
    // Give the shell time to settle (it redirects as soon as the agent state is missing).
    await page.waitForURL((u) => !u.pathname.startsWith('/dashboard'), { timeout: 5000 }).catch(() => {})
    await shot(page, 'me-failure-bounces-home.png')
    await expect(page, 'the owner is still where they were').toHaveURL(/\/dashboard\/notebook$/)
    await expect(page.getByText(/couldn.t|can.t reach|went wrong|try again/i).first()).toBeVisible()
    void owner
  })

  test('a deep link to a dashboard page survives logging in', async ({ page, owner }) => {
    await page.context().clearCookies()
    await page.goto('/dashboard/people')
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%2Fpeople$/)
    await page.getByLabel('Email').fill(owner.email)
    await page.getByLabel('Password', { exact: true }).fill(owner.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page).toHaveURL(/\/dashboard\/people$/)
    await expect(page.getByRole('heading', { level: 1, name: 'People who matter' })).toBeVisible()
  })

  test('a collapsed sidebar stays collapsed after a reload', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/people')
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeVisible()
    await page.getByRole('main').getByRole('button', { name: 'Toggle Sidebar' }).click()
    await expect(page.locator('[data-slot="sidebar"][data-state="collapsed"]')).toHaveCount(1)
    await page.reload()
    await expect(page.getByRole('heading', { level: 1, name: 'People who matter' })).toBeVisible()
    await expect(page.locator('[data-slot="sidebar"][data-state="collapsed"]')).toHaveCount(1)
  })
})

test.describe('dashboard: notebook', () => {
  test('forgetting the last note of a kind while that kind is filtered leaves a way back', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    // Pick a kind with exactly one note from the introduction (labels as the page shows them).
    const LABEL: Record<string, string> = { about: 'About you', work: 'Work', person: 'People', preference: 'Preference', urgency: 'Urgent', availability: 'Availability', sharing: 'Sharing', other: 'Note' }
    const notes = (await (await page.request.get('/api/app/notebook')).json()).memories as { category: string }[]
    const byLabel = new Map<string, number>()
    for (const n of notes) byLabel.set(LABEL[n.category] ?? 'Note', (byLabel.get(LABEL[n.category] ?? 'Note') ?? 0) + 1)
    const label = [...byLabel.entries()].find(([, n]) => n === 1)?.[0] ?? ''
    expect(label, `a kind with exactly one note: ${JSON.stringify([...byLabel])}`).not.toBe('')
    const filters = page.getByRole('group', { name: 'Filter by kind' })
    await filters.getByRole('button', { name: label, exact: true }).click()
    await expect(filters.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('button', { name: 'Forget this' })).toHaveCount(1)
    const only = page.getByRole('listitem').filter({ has: page.getByRole('button', { name: 'Forget this' }) })
    const text = (await only.locator('p').innerText()).trim()
    await page.getByRole('button', { name: 'Forget this' }).click()
    await page.getByRole('button', { name: 'Forget', exact: true }).click()
    await expect(page.getByText(text, { exact: true })).toHaveCount(0)
    await expect(filters.getByRole('button', { name: label, exact: true })).toHaveCount(0)
    await shot(page, 'notebook-filter-stuck.png')
    // The rest of the notebook is still there to see: either the filter falls back to Everything,
    // or the page at least doesn't claim a search matched nothing.
    await expect(page.getByText('Nothing matches that search.')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Forget this' }).first()).toBeVisible()
  })

  test('if the notebook cannot be loaded, the page says so and stops showing placeholders', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/notebook', serverError)
    await page.goto('/dashboard/notebook')
    await expect(page.getByText('Something went wrong on our side.')).toBeVisible()
  })
})

test.describe('dashboard: settings', () => {
  test('if your rules cannot be loaded, Settings says so instead of loading forever', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/preferences', serverError)
    let asked = false
    page.on('requestfinished', (r) => {
      if (r.url().endsWith('/api/app/preferences')) asked = true
    })
    await page.goto('/dashboard/settings')
    await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    await expect.poll(() => asked).toBe(true)
    const rules = page.locator('[data-slot="card"]').filter({ hasText: 'Your rules' })
    await rules.scrollIntoViewIfNeeded()
    await rules.screenshot({ path: `${QA}/settings-rules-spin-forever.png` })
    await expect(rules.getByText(/Something went wrong|couldn.t|try again/i)).toBeVisible({ timeout: 5000 })
  })

  test('your rules show what you said in the introduction', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/settings')
    const rules = page.locator('[data-slot="card"]').filter({ hasText: 'Your rules' })
    await expect(rules).toContainText('A family emergency')
    await expect(rules).toContainText('22:00–07:00 (Asia/Singapore), VIPs can break through')
    await expect(rules).toContainText('My home address')
  })
})

test.describe('dashboard: connections', () => {
  test('if Disconnect fails, the person is told and the service still shows as connected', async ({ page, owner }) => {
    void owner
    await plantSecret('anthropic', 'api_key', 'sk-ant-e2e-planted-placeholder-not-real-9q2b')
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await openConnections(page)
    const claude = page.getByRole('button', { name: /Claude, Ava's brain/ })
    await expect(claude).toContainText('Connected (sk-…9q2b)')
    await claude.click()
    await page.route('**/api/app/connections/anthropic/disconnect', serverError)
    await page.getByRole('button', { name: 'Disconnect Claude' }).click()
    await expect(page.getByRole('button', { name: 'Disconnect Claude' })).toBeEnabled()
    await shot(page, 'disconnect-fails-silently.png')
    expect(await storedSecrets()).toHaveLength(1)
    console.log('page errors:', JSON.stringify(errors))
    await expect(page.getByText('Something went wrong on our side.')).toBeVisible({ timeout: 5000 })
  })

  test('a rejected Telegram token says so, with what to do, and offers to reconnect', async ({ page, owner }) => {
    void owner
    await telegramRejected(page, { ownerLinked: true })
    await openConnections(page)
    const row = page.getByRole('button', { name: /^Telegram/ })
    await expect(row).toContainText('Token rejected: reconnect (@qa_example_bot)')
    await expect(row).toContainText('Reconnect')
    await row.click()
    await expect(page.getByText(/Telegram rejected the saved token/)).toBeVisible()
    await expect(page.getByText(/In BotFather, send \/token/)).toBeVisible()
    await expect(page.getByLabel('Bot token from BotFather')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Check and reconnect' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Disconnect Telegram' })).toBeVisible()
  })

  test('home does not say Telegram is working while its token is rejected', async ({ page, owner }) => {
    await telegramRejected(page, { ownerLinked: true })
    await openConnections(page)
    await expect(page.getByRole('button', { name: /^Telegram/ })).toContainText('Token rejected')
    await goToDashboardPage(page, 'Home')
    await expect(homeHeading(page, owner)).toBeVisible()
    const reach = page.locator('[data-slot="card"]').filter({ hasText: 'Where people reach Ava' })
    await reach.screenshot({ path: `${QA}/home-reach-telegram-linked.png` })
    const telegram = reach.getByRole('listitem').filter({ hasText: 'Telegram' })
    await expect(telegram).not.toContainText('Linked to you')
    await expect(telegram).toContainText(/reconnect|rejected/i)
  })

  test('if linking your own Telegram fails, the person is told', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/connections', async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.services.telegram = { connected: true, via: 'app', hints: { bot_token: '123…wxyz' }, meta: { username: 'qa_example_bot' }, health: { status: 'ok' } }
      await route.fulfill({ response: res, json: body })
    })
    await page.route('**/api/app/channels/telegram', serverError)
    await openConnections(page)
    await page.getByRole('button', { name: /^Telegram/ }).click()
    await page.getByRole('button', { name: 'Link my Telegram' }).click()
    await expect(page.getByRole('button', { name: 'Link my Telegram' })).toBeVisible()
    await shot(page, 'link-telegram-fails-silently.png')
    await expect(page.getByText('Something went wrong on our side.')).toBeVisible({ timeout: 5000 })
  })

  test('a crafted ?google=connected link does not announce a connection that does not exist', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/connections?google=connected')
    await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()
    const row = page.getByRole('button', { name: /^Google Calendar/ })
    await expect(row).toContainText('Connect')
    await expect(row).not.toContainText('Connected')
    await expect(page).toHaveURL(/\/dashboard\/connections$/)
    await shot(page, 'google-connected-spoof.png')
    await expect(page.getByText('Google Calendar is connected. I can read your calendar now, never change it.')).toHaveCount(0)
  })

  test('the Capabilities page is named Capabilities everywhere a person sees it', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/skills')
    await expect(page).toHaveURL(/\/dashboard\/capabilities$/)
    await expect(page).toHaveTitle('Capabilities | Superaide')
    await expect(page.getByRole('banner').or(page.locator('header')).first()).toContainText('Capabilities')
    await expect(page.getByRole('link', { name: 'Capabilities', exact: true })).toHaveAttribute('aria-current', 'page')
    const text = await page.getByRole('main').innerText()
    expect(text.replace(/Agent Skills/g, '')).not.toMatch(/\bskills?\b/i)
  })
})

test.describe('dashboard: people', () => {
  test('lists the people from the introduction, VIPs marked', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/people')
    const priya = page.getByRole('row').filter({ hasText: 'Priya' })
    await expect(priya).toContainText('Partner')
    await expect(priya).toContainText('VIP')
    await expect(page.getByRole('row').filter({ hasText: 'Arjun' })).not.toContainText('VIP')
  })

  test('chat still works after the People list fails to load', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/people', serverError)
    await page.goto('/dashboard/people')
    await expect(page.getByText('Something went wrong on our side.')).toBeVisible()
    await goToDashboardPage(page, 'Chat')
    await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
  })
})

