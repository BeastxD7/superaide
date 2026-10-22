// QA bug hunt: the AI model page (ADR 0007), Superaide AI credits, spend limits and what the owner
// sees when one is reached.
//
// Two passes on one slot:
//  - the default stack (FAKE_LLM=true), started by Playwright: `E2E_SLOT=4 npx playwright test qa-models`
//  - "credits" (E2E_QA_CREDITS=1): an API started by hand with FAKE_LLM=false, PLATFORM_AI_ENABLED=true, a
//    made-up platform key, and ANTHROPIC_BASE_URL pointing at a fake Messages API on 127.0.0.1, so the real
//    credits path (budget guard, allowance, platform cap) runs and nothing leaves the machine. Playwright
//    reuses that API (reuseExistingServer). Start it with e2e/scripts/qa-models/start-real.sh (extra env: PLATFORM_*, BUDGET_*); see that script.
import { execFileSync } from 'node:child_process'
import { createServer, type Server } from 'node:http'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { AddressInfo } from 'node:net'
import type { Page } from '@playwright/test'
import { expect, newPerson, query, test } from '../src/fixtures'
import { DATABASE_URL, SLOT } from '../src/env'
import { Admin, composer, goToDashboardPage, openChat, sendButton } from '../src/helpers/workspace'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/models`
const HERE = fileURLToPath(new URL('.', import.meta.url))
const HELPERS = resolve(HERE, '../scripts/qa-models')
const BACKEND_DIR = resolve(HERE, '../../backend')
const CREDITS = process.env.E2E_QA_CREDITS === '1'
// The default stack with DEPLOY_MODE=hosted passed through to the API (E2E_QA_HOSTED=1 DEPLOY_MODE=hosted).
const HOSTED = process.env.E2E_QA_HOSTED === '1' && process.env.DEPLOY_MODE === 'hosted'
const DEFAULT_PASS = !CREDITS && !HOSTED && process.env.E2E_QA_UNLIMITED !== '1' && process.env.E2E_QA_NOCAP !== '1'
// The credits API again, with PLATFORM_OWNER_DAILY_USD=0 and PLATFORM_OWNER_MONTHLY_USD=0 (no per-owner limit).
const UNLIMITED = process.env.E2E_QA_UNLIMITED === '1'
// The credits API again, with PLATFORM_DAILY_CAP_USD left at its default of 0.
const NO_CAP = process.env.E2E_QA_NOCAP === '1'

async function openModels(page: Page) {
  await page.goto('/dashboard/chat')
  await goToDashboardPage(page, 'AI model')
  await expect(page.getByRole('heading', { level: 1, name: 'AI model' })).toBeVisible()
  // The settings have loaded once the provider cards are there (the credits card renders with them).
  await expect(page.getByRole('listitem', { name: 'Claude (Anthropic)', exact: true })).toBeVisible()
}

async function instance(): Promise<{ id: string; tenant_id: string }> {
  const rows = await query<{ id: string; tenant_id: string }>("SELECT id, tenant_id FROM agent_instance WHERE status = 'active'")
  return rows[0]
}

// Credits already committed by this Ava (settled), `hoursAgo` hours ago.
async function spentCredits(usd: number, hoursAgo = 1, status: 'settled' | 'reserved' = 'settled'): Promise<void> {
  const inst = await instance()
  await query(
    `INSERT INTO spend_ledger (id, tenant_id, agent_id, provider, purpose, model, status, reserved_usd, cost_usd,
       input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, key_source, created_at)
     VALUES (gen_random_uuid(), $1, $2, 'anthropic', 'chat_turn', 'claude-haiku-5-5', $3, $4, $5, 0, 0, 0, 0, 'platform',
       now() - make_interval(hours => $6))`,
    [inst.tenant_id, inst.id, status, usd, status === 'settled' ? usd : 0, hoursAgo],
  )
}

// Superaide's shared allowance for today (PLATFORM_DAILY_CAP_USD=5 in the credits pass) is used up.
async function platformCapUsedUp(): Promise<void> {
  await query(
    `INSERT INTO platform_spend (day, committed_usd, updated_at) VALUES ((now() AT TIME ZONE 'UTC')::date, 5, now())
     ON CONFLICT (day) DO UPDATE SET committed_usd = 5`,
  )
}

// A screenshot of the chat with Ava's latest reply (or refusal) scrolled into view.
async function chatShot(page: Page, name: string): Promise<void> {
  await page.getByText(REFUSAL).or(page.getByText(FAKE_REPLY)).last().scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${QA}/${name}.png`, fullPage: true })
}

async function optIn(page: Page) {
  await openModels(page)
  const card = page.getByRole('region', { name: 'Superaide AI credits' })
  await card.getByRole('button', { name: 'Use Superaide AI credits' }).click()
  await expect(card.getByText('On', { exact: true })).toBeVisible()
  return card
}

async function say(page: Page, text: string) {
  await openChat(page)
  await composer(page).fill(text)
  await sendButton(page).click()
  await expect(page.getByText(text, { exact: true })).toBeVisible()
}

const OUT_OF_CREDITS = "You've used today's Superaide credits. Add your own AI key on the AI model page, or try tomorrow."
const FAKE_REPLY = 'Fake Claude here: noted.'
// Every reply Ava gives when a limit stops a model call (pipeline._budget_reply).
const REFUSAL = /thinking budget|this month's budget|Superaide credits\. Add|spending cap, so I'm paused/

// The owner's own (made-up) Claude key, stored in their vault as the AI model page would after a check.
// (The page's own check calls api.anthropic.com, so it is skipped here; the key only ever reaches the fake.)
async function ownClaudeKey(key = 'sk-ant-own-fake-key-0123456789'): Promise<void> {
  const inst = await instance()
  execFileSync('uv', ['run', '--quiet', '--project', BACKEND_DIR, 'python', `${HELPERS}/own_key.py`, inst.tenant_id, inst.id, key], {
    env: { ...process.env, DATABASE_URL, SECRETS_MASTER_KEY: Buffer.alloc(32, SLOT + 1).toString('base64') },
  })
}

test.describe('credits', () => {
  test.skip(!CREDITS, 'needs the credits API (E2E_QA_CREDITS=1)')

  test('credits pay for a reply, and the card counts what was used', async ({ page, owner }) => {
    void owner
    await optIn(page)
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(FAKE_REPLY)).toBeVisible()
    const rows = await query<{ key_source: string; status: string }>('SELECT key_source, status FROM spend_ledger')
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.every((r) => r.key_source === 'platform' && r.status === 'settled')).toBe(true)
    await spentCredits(0.2, 1)
    await spentCredits(0.1, 30) // older than a day: counts for the month only
    await openModels(page)
    await expect(page.getByText(/Used in the last 24 hours: US\$0\.20 of US\$0\.50 · last 30 days: US\$0\.30 of US\$5\.00/)).toBeVisible()
  })

  test('the daily allowance the card shows can all be used for conversation', async ({ page, owner }) => {
    void owner
    await optIn(page)
    await spentCredits(0.4) // four fifths of the US$0.50 the card offers
    await openModels(page)
    await expect(page.getByText(/Used in the last 24 hours: US\$0\.40 of US\$0\.50/)).toBeVisible()
    await say(page, 'Draft a note to Arjun about Friday.')
    const reply = page.getByText(FAKE_REPLY).or(page.getByText(REFUSAL))
    await expect(reply.last()).toBeVisible()
    await chatShot(page, 'credits-bucket-cut-short')
    await expect(page.getByText(FAKE_REPLY)).toBeVisible()
  })

  test('when today\'s credits are used up, Ava says so and points to the AI model page', async ({ page, owner }) => {
    void owner
    await optIn(page)
    await spentCredits(0.5) // the whole US$0.50 daily allowance
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(REFUSAL).last()).toBeVisible()
    await chatShot(page, 'credits-used-up-message')
    await expect(page.getByText(FAKE_REPLY)).toHaveCount(0)
    await expect(page.getByText(OUT_OF_CREDITS)).toBeVisible()
  })

  test('credit-paid calls are charged at Superaide\'s price, whatever price the owner sets', async ({ page, owner, api }) => {
    void owner
    await optIn(page)
    // The owner's own price table, through the same API the page uses (any signed-in owner can send it).
    const priced = await api.post('/app/providers/prices', {
      provider: 'anthropic', model: 'claude-haiku-5-5', input_usd_per_mtok: 0, output_usd_per_mtok: 0,
    })
    expect(priced.status()).toBe(200)
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(FAKE_REPLY)).toBeVisible()
    await say(page, 'And one to Priya about dinner.')
    await expect(page.getByText(FAKE_REPLY)).toHaveCount(2)
    await openModels(page)
    await expect(page.getByText(/Used in the last 24 hours: US\$0\.00 of US\$0\.50/)).toBeVisible()
    await page.screenshot({ path: `${QA}/credits-free-price.png`, fullPage: true })
    const rows = await query<{ purpose: string; reserved_usd: string; cost_usd: string }>(
      "SELECT purpose, reserved_usd, cost_usd FROM spend_ledger WHERE key_source = 'platform' ORDER BY created_at",
    )
    console.log('credit-paid calls', JSON.stringify(rows))
    expect(rows.length).toBeGreaterThan(0)
    // Correct: each credit-paid call reserves and costs Superaide's real price (it pays Anthropic for it).
    for (const r of rows) {
      expect(Number(r.reserved_usd)).toBeGreaterThan(0)
      expect(Number(r.cost_usd)).toBeGreaterThan(0)
    }
  })

  test('after this month\'s credits are used, the owner\'s own key carries on, as Ava advises', async ({ page, owner }) => {
    void owner
    await optIn(page)
    await spentCredits(5, 72) // the whole US$5 monthly allowance, three days ago
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(REFUSAL).last()).toBeVisible()
    await chatShot(page, 'own-key-before')
    // "Add your own AI key on the AI model page to carry on": the owner does.
    await ownClaudeKey()
    await openModels(page)
    await expect(page.getByRole('listitem', { name: 'Claude (Anthropic)', exact: true }).getByText('Connected', { exact: true })).toBeVisible()
    await page.screenshot({ path: `${QA}/own-key-connected.png`, fullPage: true })
    await say(page, 'And one to Priya about dinner.')
    await expect(page.getByText(REFUSAL).or(page.getByText(FAKE_REPLY)).last()).toBeVisible()
    await chatShot(page, 'own-key-still-refused')
    // Correct: the owner's own key answers, paid by the owner (key_source "owner").
    await expect(page.getByText(FAKE_REPLY)).toBeVisible()
    expect(await query("SELECT 1 FROM spend_ledger WHERE key_source = 'owner' AND status = 'settled'")).not.toEqual([])
  })

  test('a contact writing in when the credits have run out is not told to add an AI key', async ({ page, owner }) => {
    void owner
    await optIn(page)
    const admin = new Admin(page.request)
    // Priya writes on Telegram from an account the operator linked to her.
    const priyaSays = (text: string) => admin.post('/simulate', { provider: 'telegram', sender_id: '7001', sender_display: 'Priya', text })
    expect((await priyaSays('hi')).ok()).toBe(true)
    const [identity] = await query<{ id: string }>("SELECT id FROM channel_identity WHERE stable_key = '7001'")
    const [priya] = await query<{ id: string }>("SELECT id FROM person WHERE display_name = 'Priya'")
    const link = await page.request.post(`/api/api/identities/${identity.id}/link`, {
      data: { person_id: priya.id },
      headers: { Authorization: 'Bearer e2e-admin-token', 'X-Operator-Reason': 'QA: link a test contact', 'X-Operator-Instance': (await instance()).id },
    })
    expect(link.ok()).toBe(true)
    const before = (await (await priyaSays('Is Shashank free on Friday?')).json()) as { mode: string; reply: string }
    expect(before.mode).toBe('known')
    // Superaide's shared allowance for today is used up.
    await platformCapUsedUp()
    const after = (await (await priyaSays('Is he free on Saturday?')).json()) as { mode: string; reply: string; outbox: { to_context: string; text: string }[] }
    console.log('reply to Priya:', JSON.stringify(after.outbox))
    expect(after.outbox.map((m) => m.to_context)).toEqual(['7001'])
    // Correct: Priya isn't the one who can add a key; she gets a holding reply, not the owner's instructions.
    expect(after.reply).not.toMatch(/Superaide credits|AI key|AI model page/)
  })

  test('when Superaide\'s shared daily cap is used up, the owner is not told they used their own credits', async ({ page, owner }) => {
    void owner
    await optIn(page)
    // Other owners have used the server's whole shared allowance (PLATFORM_DAILY_CAP_USD=5) today.
    await platformCapUsedUp()
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(/Superaide credits/).last()).toBeVisible()
    await chatShot(page, 'credits-platform-cap')
    await openModels(page)
    await expect(page.getByText(/Used in the last 24 hours: US\$0\.00 of US\$0\.50/)).toBeVisible()
    await page.screenshot({ path: `${QA}/credits-platform-cap-card.png`, fullPage: true })
    // Correct: the reply says Superaide's shared allowance ran out, not the owner's own.
    await openChat(page)
    await expect(page.getByText(OUT_OF_CREDITS)).toHaveCount(0)
  })
})

test.describe('credits with no per-owner allowance', () => {
  test.skip(!UNLIMITED, 'needs the credits API with PLATFORM_OWNER_DAILY_USD=0 (E2E_QA_UNLIMITED=1)')

  test('the card does not say the allowance is US$0.00 when there is no per-owner limit', async ({ page, owner }) => {
    void owner
    const card = await optIn(page)
    // The guard really has no per-owner limit: with US$0.30 of credits used today, a reply still goes through.
    await spentCredits(0.3, 1)
    await say(page, 'Draft a note to Arjun about Friday.')
    await expect(page.getByText(FAKE_REPLY)).toBeVisible()
    await openModels(page)
    await page.screenshot({ path: `${QA}/credits-zero-allowance.png`, fullPage: true })
    await expect(card).toBeVisible()
    await expect(card).not.toContainText('US$0.00 a day')
    await expect(card).not.toContainText('of US$0.00')
  })
})

test.describe('credits switched on with the platform cap left at 0', () => {
  test.skip(!NO_CAP, 'needs the credits API with PLATFORM_DAILY_CAP_USD unset (E2E_QA_NOCAP=1)')

  test('credits that can never pay for a call are not offered', async ({ page, owner }) => {
    void owner
    await openModels(page)
    const card = page.getByRole('region', { name: 'Superaide AI credits' })
    if ((await card.count()) > 0) {
      await card.getByRole('button', { name: 'Use Superaide AI credits' }).click()
      await expect(card.getByText('On', { exact: true })).toBeVisible()
      await expect(page.getByText(/Ava is thinking with Superaide AI credits/)).toBeVisible()
      await page.screenshot({ path: `${QA}/nocap-offered.png`, fullPage: true })
      await say(page, 'Draft a note to Arjun about Friday.')
      await expect(page.getByText(REFUSAL).or(page.getByText(FAKE_REPLY)).last()).toBeVisible()
      await chatShot(page, 'nocap-first-message')
    }
    // Correct: no credits are offered while the platform can't spend anything on them.
    await openModels(page)
    await page.screenshot({ path: `${QA}/nocap-end.png`, fullPage: true })
    await expect(page.getByRole('region', { name: 'Superaide AI credits' })).toHaveCount(0)
  })
})

// --- the default stack (FAKE_LLM=true, started by Playwright) ---------------------------------------

// A tiny OpenAI-compatible model server on this machine (lists one model).
function fakeModelServer(): Promise<{ server: Server; url: string }> {
  const server = createServer((req, res) => {
    if (req.url?.endsWith('/models')) {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ object: 'list', data: [{ id: 'tiny-llm', object: 'model', created: 1, owned_by: 'me' }] }))
      return
    }
    res.writeHead(404).end()
  })
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1` })),
  )
}

function card(page: Page, name: string) {
  return page.getByRole('listitem', { name, exact: true })
}

test.describe('AI model page', () => {
  test.skip(!DEFAULT_PASS, 'runs on the default stack')

  test('a second endpoint cannot take another provider\'s name', async ({ page, owner, api }) => {
    void owner
    const { server, url } = await fakeModelServer()
    try {
      const statuses: Record<string, number> = {}
      for (const key of ['openai', 'anthropic', 'platform']) {
        statuses[key] = (await api.post('/app/providers/custom', { base_url: url, key })).status()
      }
      console.log('custom endpoint saved under other names:', JSON.stringify(statuses))
      await openModels(page)
      await page.screenshot({ path: `${QA}/endpoint-named-openai.png`, fullPage: true })
      // Correct: each is refused, and the OpenAI and Claude cards stay unconnected.
      expect(await query('SELECT key FROM llm_provider')).toEqual([])
      for (const key of ['openai', 'anthropic', 'platform']) expect(statuses[key], key).toBeGreaterThanOrEqual(400)
      await expect(card(page, 'OpenAI').getByText('Connected', { exact: true })).toHaveCount(0)
    } finally {
      server.close()
    }
  })

  test('after connecting only a local server, Save models works without touching the pickers', async ({ page, owner }) => {
    void owner
    const { server, url } = await fakeModelServer()
    try {
      await openModels(page)
      const custom = card(page, 'Other OpenAI-compatible service')
      await custom.getByRole('button', { name: 'Connect' }).click()
      await custom.getByLabel('Address (base URL)').fill(url)
      await custom.getByRole('button', { name: 'Save' }).click()
      await expect(custom.getByText('Connected', { exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Save models' }).click()
      const models = page.getByRole('region', { name: 'Models' })
      await expect(models.getByText('Saved.').or(models.getByText(/Connect .* first/))).toBeVisible()
      await models.scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${QA}/roles-connect-anthropic-first.png`, fullPage: true })
      // Correct: the only connected provider is picked, and saving works.
      await expect(page.getByRole('group', { name: 'Conversation' }).getByLabel('Provider')).toHaveValue('custom')
      await expect(models.getByText('Saved.')).toBeVisible()
    } finally {
      server.close()
    }
  })

  test('a price that is not a number gets a plain message', async ({ page, owner }) => {
    void owner
    // The page as it is with OpenRouter connected and a model whose price Superaide doesn't know
    // (stubbed in the browser: connecting OpenRouter for real would call openrouter.ai).
    await page.route('**/api/app/providers', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      const res = await route.fetch()
      const body = await res.json()
      body.providers = [{ key: 'openrouter', preset: 'openrouter', base_url: 'https://openrouter.ai/api/v1', enabled: true, key_hint: 'sk-or…abcd', models: ['acme/new-model'], checked_at: null }]
      body.priced = { anthropic: body.priced.anthropic, openrouter: [] }
      await route.fulfill({ response: res, json: body })
    })
    await openModels(page)
    const chat = page.getByRole('group', { name: 'Conversation' })
    await chat.getByLabel('Provider').selectOption('openrouter')
    await expect(chat.getByText("Superaide doesn't know this model's price")).toBeVisible()
    await chat.getByLabel('Input, US$ / 1M tokens').fill('1,5')
    await chat.getByLabel('Output, US$ / 1M tokens').fill('2')
    await page.getByRole('button', { name: 'Save models' }).click()
    const models = page.getByRole('region', { name: 'Models' })
    await expect(models.getByText(/Decimal input|number|price/).last()).toBeVisible()
    await models.scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${QA}/price-not-a-number.png`, fullPage: true })
    expect(await query('SELECT 1 FROM owner_model_price')).toEqual([])
    await expect(models.getByText(/Decimal input|integer, float, string/)).toHaveCount(0)
  })

  test('the settings are the owner\'s alone: signed out, not hired, or without the CSRF header, every change is refused', async ({ page, api, browser }) => {
    const writes: [string, unknown][] = [
      ['/app/providers/credits', { on: true }],
      ['/app/providers/prices', { provider: 'anthropic', model: 'claude-haiku-5-5', input_usd_per_mtok: 0, output_usd_per_mtok: 0 }],
      ['/app/providers/web-search/remove', {}],
      ['/app/providers/custom/remove', {}],
      ['/app/providers/custom', { base_url: 'http://127.0.0.1:9/v1' }],
    ]
    // Signed out.
    for (const [path, body] of writes) expect((await api.post(path, body)).status(), path).toBe(401)
    expect((await api.get('/app/providers')).status()).toBe(401)
    // Signed in, but hasn't hired Ava.
    await api.signup(newPerson('Not Hired'))
    for (const [path, body] of writes) expect((await api.post(path, body)).status(), path).toBe(403)
    const put = await page.request.put('/api/app/providers/roles', { data: { chat: { provider: 'anthropic', model: 'claude-haiku-5-5' } }, headers: { 'X-Requested-With': 'superaide-e2e' } })
    expect(put.status()).toBe(403)
    // An owner, but the request has no CSRF header.
    await api.hire()
    for (const [path, body] of writes) expect((await api.postWithoutCsrfHeader(path, body)).status(), path).toBe(403)
    expect(await query('SELECT 1 FROM owner_model_price')).toEqual([])
    expect(await query("SELECT key_source FROM agent_instance")).toEqual([{ key_source: 'owner' }])
    void browser
  })

  test('credits can\'t be switched on when the server does not offer them, and the page does not mention them', async ({ page, owner, api }) => {
    void owner
    const res = await api.post('/app/providers/credits', { on: true })
    expect(res.status()).toBe(409)
    expect((await res.json()).detail).toBe("Superaide AI credits aren't offered on this server.")
    await openModels(page)
    await expect(page.getByRole('region', { name: 'Superaide AI credits' })).toHaveCount(0)
    await expect(page.getByText(/Superaide AI credits/)).toHaveCount(0)
  })

  test('a refused key is never sent back, and nothing is stored', async ({ owner, api }) => {
    void owner
    const longKey = `sk-local-${'x'.repeat(420)}`
    for (const [path, body] of [
      ['/app/providers/custom', { api_key: longKey, base_url: 'http://127.0.0.1:9/v1' }],
      ['/app/providers/test', { preset: 'custom', api_key: longKey, base_url: 'http://127.0.0.1:9/v1' }],
      ['/app/providers/web-search', { api_key: `BSA${'y'.repeat(300)}` }],
    ] as [string, unknown][]) {
      const res = await api.post(path, body)
      expect(res.status(), path).toBe(422)
      const text = await res.text()
      expect(text).not.toContain('xxxxxxxxxx')
      expect(text).not.toContain('yyyyyyyyyy')
    }
    expect(await query('SELECT 1 FROM llm_provider')).toEqual([])
  })
})

test.describe('AI model page on a hosted server', () => {
  test.skip(!HOSTED, 'needs DEPLOY_MODE=hosted (E2E_QA_HOSTED=1)')

  test('a model server on the host itself or its private network is refused, plainly', async ({ page, owner, api }) => {
    void owner
    const { server, url } = await fakeModelServer()
    try {
      await openModels(page)
      await page.screenshot({ path: `${QA}/hosted-page.png`, fullPage: true })
      for (const name of ['Ollama (on this computer)', 'Other OpenAI-compatible service']) {
        const c = card(page, name)
        await c.getByRole('button', { name: 'Connect' }).click()
        await c.getByLabel('Address (base URL)').fill(url)
        await c.getByRole('button', { name: 'Save' }).click()
        await expect(c.getByText(/That address is on a private network/)).toBeVisible()
        await c.getByLabel('Address (base URL)').fill('http://10.0.0.7:11434/v1')
        await c.getByRole('button', { name: 'Test connection' }).click()
        await expect(c.getByText(/That address is on a private network/)).toBeVisible()
      }
      for (const preset of ['ollama', 'vllm', 'custom']) {
        const res = await api.post('/app/providers/test', { preset, base_url: url })
        expect(res.status(), preset).toBe(400)
      }
      expect(await query('SELECT 1 FROM llm_provider')).toEqual([])
    } finally {
      server.close()
    }
  })
})
