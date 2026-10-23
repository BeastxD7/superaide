// Forms, navigation, stale lists and dates (issues #77, #79, #97). Server answers that the real fake-model
// backend can't produce on demand are scripted with page.route; everything else runs against the real stack.
import type { Page, Route } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { triageOn } from '../src/helpers/triage'

const iso = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString()

const SETTINGS = {
  ladder: [],
  expire_after_s: 3600,
  max_per_hour: 3,
  max_per_day: 10,
  vip_ignores_budget: 'critical',
  theta_interrupt: 0.7,
  theta_handle: 0.3,
  silenced_people: [],
  silenced_topics: [],
  autonomy_grants: [],
  stranger_intake: false,
  away_until: null,
  away_visibility: 'everyone',
  busy_until: null,
  tell_sender_seen: false,
  phone_enabled: false,
}

const item = (id: string, createdAt: string) => ({
  id,
  decision: 'handle',
  reason: 'A routine question',
  sender: 'Lee',
  quotes: [],
  rule_hits: [],
  stage: 'infant',
  maybe_urgent: false,
  shadow_decision: null,
  feedback: null,
  created_at: createdAt,
  escalation: null,
})

async function mockNeedsYou(page: Page, handled: unknown[] = []) {
  await triageOn(page)
  await page.route('**/api/app/needs-you', (r) =>
    r.fulfill({ json: { needs_you: [], handled, held: [], counts: { needs_you: 0, handled: handled.length, held: 0 }, stage: 'infant' } }),
  )
}

test('a blank interrupt limit is refused instead of silently saved as 0', async ({ page, owner }) => {
  void owner
  await mockNeedsYou(page)
  await page.route('**/api/app/triage/settings', (r: Route) => {
    if (r.request().method() === 'PUT') {
      puts.push(r.request().postDataJSON() as Record<string, unknown>)
      return r.fulfill({ json: SETTINGS })
    }
    return r.fulfill({ json: SETTINGS })
  })
  const puts: Record<string, unknown>[] = []
  await page.goto('/dashboard/needs-you')
  const hour = page.getByLabel('Interrupts per hour, at most')
  await hour.fill('')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await page.waitForTimeout(400)
  expect(puts).toHaveLength(0)
  await expect(hour).toHaveJSProperty('validity.valueMissing', true)
  // A whole number goes through as a number.
  await hour.fill('5')
  await page.getByLabel('Interrupts per day, at most').fill('20')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved.')).toBeVisible()
  expect(puts[0]).toMatchObject({ max_per_hour: 5, max_per_day: 20 })
})

test('Needs-you dates carry the year once they are older than a week', async ({ page, owner }) => {
  void owner
  await mockNeedsYou(page, [item('h1', iso(40)), item('h2', iso(0))])
  await page.goto('/dashboard/needs-you')
  await page.locator('button, [role="tab"]').filter({ hasText: /^Handled for you/ }).click()
  const year = new Date(iso(40)).getFullYear().toString()
  await expect(page.getByText(new RegExp(`${year}, `))).toBeVisible()
  await expect(page.getByText(/Today, /)).toBeVisible()
})

test('AI model prices accept a comma decimal, and Save needs a model name', async ({ page, owner }) => {
  void owner
  const prices: Record<string, unknown>[] = []
  await page.route('**/api/app/providers', async (r) => {
    const real = await r.fetch()
    const body = (await real.json()) as Record<string, unknown> & { roles: Record<string, { provider: string; model: string }> }
    body.providers = [{ key: 'ollama', preset: 'ollama', base_url: 'http://127.0.0.1:11434/v1', enabled: true, key_hint: '', models: [], checked_at: null }]
    body.roles = { chat: { provider: 'ollama', model: '' }, triage: { provider: 'ollama', model: '' }, deep: { provider: 'ollama', model: '' } }
    await r.fulfill({ json: body })
  })
  await page.route('**/api/app/providers/prices', (r) => {
    prices.push(r.request().postDataJSON() as Record<string, unknown>)
    return r.fulfill({ json: {} })
  })
  await page.route('**/api/app/providers/roles', (r) => r.fulfill({ json: {} }))
  await page.goto('/dashboard/models')
  const region = page.getByRole('region', { name: 'Models' })
  const save = region.getByRole('button', { name: 'Save models' })
  await expect(save).toBeDisabled() // no model name yet
  for (const role of ['chat', 'triage', 'deep']) await region.locator(`#${role}-model`).fill('my-local-model')
  await expect(save).toBeEnabled()
  for (const role of ['chat', 'triage', 'deep']) {
    await region.locator(`#${role}-in`).fill('0,15')
    await region.locator(`#${role}-out`).fill('0,60')
  }
  await save.click()
  await expect(region.getByText('Saved.')).toBeVisible()
  expect(prices[0]).toMatchObject({ input_usd_per_mtok: 0.15, output_usd_per_mtok: 0.6 })
})

test('Settings shows an unverified address and can send the link again', async ({ page, owner }) => {
  void owner
  let sent = 0
  await page.route('**/api/auth/verify-email/send', (r) => {
    sent += 1
    return r.fulfill({ json: { status: 'sent' } })
  })
  await page.goto('/dashboard/settings')
  await expect(page.getByText('Not verified')).toBeVisible()
  await page.getByRole('button', { name: 'Send the link again' }).click()
  await expect(page.getByText('Sent. Check your inbox.')).toBeVisible()
  expect(sent).toBe(1)
})

test('Sign out everywhere asks first and does nothing until confirmed', async ({ page, owner }) => {
  void owner
  let calls = 0
  await page.route('**/api/auth/logout-everywhere', (r) => {
    calls += 1
    return r.fulfill({ json: { status: 'ok' } })
  })
  await page.goto('/dashboard/settings')
  await page.getByRole('button', { name: 'Sign out everywhere' }).click()
  await expect(page.getByText('This signs you out on every device')).toBeVisible()
  expect(calls).toBe(0)
  await page.getByRole('button', { name: 'Keep it' }).click()
  await expect(page.getByRole('button', { name: 'Sign out everywhere' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out everywhere' }).click()
  await page.getByRole('button', { name: 'Yes, sign out everywhere' }).click()
  await expect.poll(() => calls).toBe(1)
})

test('revoking the Ask link asks first', async ({ page, owner }) => {
  void owner
  let revoked = 0
  const view = {
    link: { handle: 'e2e-handle', path: '/a/e2e-handle', published: true, revoked: false, show_first_name: false, display: 'Ava' },
    suggested: 'x-y-z',
    first_name: 'S',
    notice: '',
    stats: { views: 0, clicks: 0, submissions: 0 },
  }
  await page.route('**/api/app/ask-link', (r) => r.fulfill({ json: view }))
  await page.route('**/api/app/ask-link/requests', (r) => r.fulfill({ json: { requests: [] } }))
  await page.route('**/api/app/ask-link/revoke', (r) => {
    revoked += 1
    return r.fulfill({ json: { ...view, link: { ...view.link, revoked: true } } })
  })
  await page.goto('/dashboard/settings')
  await page.getByRole('button', { name: 'Revoke', exact: true }).click()
  expect(revoked).toBe(0)
  await page.getByRole('button', { name: 'Yes, revoke it for good' }).click()
  await expect.poll(() => revoked).toBe(1)
})

test('an approval decided in one card is no longer pending in the other, and a failed decision reloads the list', async ({ page, owner }) => {
  void owner
  let pending = true
  const approval = { id: 'ap1', kind: 'ask_reply', summary: 'Reply to Lee', requested_by: null, status: 'pending', result: null, at: iso(0), decided_at: null }
  const request = {
    id: 'rq1', name: 'Lee Wong', contact: 'lee@example.org', contact_kind: 'email', topic: 'Speaking', why: '', deadline: null,
    rank: 'normal', rule_hits: [], status: 'new', created_at: iso(0),
    reply: { approval_id: 'ap1', status: 'pending', text: 'Happy to.' },
  }
  await page.route('**/api/app/approvals', (r) => r.fulfill({ json: pending ? [approval] : [] }))
  await page.route('**/api/app/ask-link/requests', (r) => r.fulfill({ json: { requests: pending ? [request] : [] } }))
  await page.route('**/api/app/ask-link', (r) => r.fulfill({ json: { link: null, suggested: 'a-b-c', first_name: 'S', notice: '', stats: { views: 0, clicks: 0, submissions: 0 } } }))
  await page.route('**/api/app/approvals/ap1/approve', (r) => {
    pending = false
    return r.fulfill({ json: {} })
  })
  await page.goto('/dashboard')
  const waiting = page.getByText(/won.t do any of this until you say yes/)
  await expect(waiting).toBeVisible()
  await page.getByRole('button', { name: 'Approve reply' }).click()
  await expect(waiting).toHaveCount(0) // the other card read again
})

test('a decision that fails because the list was stale reads the list again', async ({ page, owner }) => {
  void owner
  let pending = true
  const approval = { id: 'ap2', kind: 'ask_reply', summary: 'Reply to Lee', requested_by: null, status: 'pending', result: null, at: iso(0), decided_at: null }
  await page.route('**/api/app/approvals', (r) => r.fulfill({ json: pending ? [approval] : [] }))
  await page.route('**/api/app/approvals/ap2/approve', (r) => {
    pending = false // decided elsewhere meanwhile
    return r.fulfill({ status: 409, json: { detail: 'That was already decided.' } })
  })
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Yes, go ahead' }).click()
  await expect(page.getByText(/won.t do any of this until you say yes/)).toHaveCount(0)
})

test('Back is not trapped by the guard redirects on /login', async ({ page, owner }) => {
  void owner
  await page.goto('/terms')
  await page.goto('/login') // signed in: sent on to the dashboard
  await expect(page).toHaveURL(/\/dashboard/)
  await page.goBack()
  await expect(page).toHaveURL(/\/terms$/)
})

test('a contact whose name starts with an emoji shows the whole emoji as the avatar', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/people', (r) =>
    r.fulfill({ json: { people: [{ id: 'p1', name: '🙂 Priya', relationship: 'partner', vip: true, channels: [] }] } }),
  )
  await page.goto('/dashboard')
  await expect(page.getByText('🙂', { exact: true })).toBeVisible()
  await page.goto('/dashboard/people')
  await expect(page.getByText('🙂', { exact: true })).toBeVisible()
})
