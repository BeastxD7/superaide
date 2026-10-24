// QA: two or more owners on one deployment (ADR 0009). Owner B tries to see, change or infer
// owner A's data through /api/* with B's own cookies and A's ids; a signed-out visitor tries the
// private surfaces; a stranger attacks the public "Ask my Ava" link; the operator's break-glass
// look must land in the right owner's Access log. Each owner has their own browser context.
//
// The Ask link tests need ASK_LINK_ENABLED: run with E2E_ASK_LINK=1.
import type { Browser, BrowserContext, Page } from '@playwright/test'
import { Api, expect, newPerson, query, test, type Person } from '../../src/fixtures'
import { ADMIN_TOKEN, ASK_LINK, BASE_URL } from '../../src/env'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/isolation`
const SECRET_A = 'my locker code is zq4417'
const SECRET_WORDS = 'locker code' // not the marker digits: a random id or timestamp can contain hex digits (markers carry a non-hex prefix, zq4417)
const OPERATOR = { Authorization: `Bearer ${ADMIN_TOKEN}` }

type Owner = { person: Person; api: Api; context: BrowserContext }

async function account(browser: Browser, name: string): Promise<Owner> {
  const context = await browser.newContext({ baseURL: BASE_URL })
  const api = new Api(context.request)
  const person = newPerson(name)
  await api.signup(person)
  return { person, api, context }
}

async function introduced(browser: Browser, name: string): Promise<Owner> {
  const o = await account(browser, name)
  await o.api.hire()
  await o.api.completeIntroduction()
  return o
}

async function instanceOf(o: Owner): Promise<string> {
  const [row] = await query<{ id: string }>(
    'SELECT i.id FROM agent_instance i JOIN user_account u ON u.id = i.owner_user_id WHERE u.email = $1',
    [o.person.email],
  )
  return row.id
}

test.beforeEach(async ({ api }) => {
  void api // every test starts from an empty database
})

test.describe('owner B with A’s ids', () => {
  test('cannot snooze, finish or cancel A’s reminder', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const rem = await a.api.ok<{ id: string }>(a.api.post('/app/reminders', { text: 'Call the bank tomorrow at 5pm' }))
    expect((await b.api.post(`/app/reminders/${rem.id}/snooze`, { minutes: 5 })).status()).toBe(404)
    expect((await b.api.post(`/app/reminders/${rem.id}/done`)).status()).toBe(404)
    expect((await b.api.post(`/app/reminders/${rem.id}/cancel`)).status()).toBe(404)
    const left = await a.api.ok<{ reminders: { id: string; status: string; due_at: string }[] }>(a.api.get('/app/reminders'))
    expect(left.reminders.find((r) => r.id === rem.id)?.status).toBe('open')
  })

  test('cannot list or remove A’s shared file, and sharing the same file says nothing about A', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const file = { name: 'cv.txt', mimeType: 'text/plain', buffer: Buffer.from('Ananya Menon. Project NIGHTJAR. PebbleRoad.') }
    const up = await a.context.request.post('/api/app/intro/files', {
      multipart: { file },
      headers: { 'X-Requested-With': 'superaide-e2e' },
    })
    const doc = (await up.json()) as { document_id: string }
    expect(JSON.stringify(await b.api.ok(b.api.get('/app/intro/documents')))).not.toContain('NIGHTJAR')
    expect((await b.api.post(`/app/intro/documents/${doc.document_id}/remove`)).status()).toBe(404)
    const again = await b.context.request.post('/api/app/intro/files', {
      multipart: { file },
      headers: { 'X-Requested-With': 'superaide-e2e' },
    })
    expect(((await again.json()) as { duplicate: boolean }).duplicate).toBe(false) // A's copy isn't B's
    const docsA = await a.api.ok<{ id: string }[]>(a.api.get('/app/intro/documents'))
    expect(docsA.map((d) => d.id)).toContain(doc.document_id)
  })

  test('cannot confirm or unlink A’s linked accounts', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const ids = await query<{ id: string }>(
      'SELECT ci.id FROM channel_identity ci JOIN user_account u ON u.person_id = ci.person_id WHERE u.email = $1',
      [a.person.email],
    )
    expect(ids.length).toBeGreaterThan(0)
    for (const { id } of ids) {
      expect((await b.api.post(`/app/channels/linked/${id}/confirm`)).status()).toBe(404)
      expect((await b.api.post(`/app/channels/linked/${id}/unlink`)).status()).toBe(404)
    }
    const still = await query<{ person_id: string | null }>('SELECT person_id FROM channel_identity WHERE id = $1', [ids[0].id])
    expect(still[0].person_id).not.toBeNull()
  })

  test('B’s own lists hold nothing of A’s', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    await a.api.ok(a.api.post('/app/chat', { text: `Remember that ${SECRET_A}` }))
    for (const path of ['/app/me', '/app/notebook', '/app/people', '/app/preferences', '/app/messages', '/app/approvals',
      '/app/access-log', '/app/intro/documents', '/app/onboarding', '/app/channels/linked']) {
      const seen = await (await b.api.get(path)).text()
      expect(seen, path).not.toContain(SECRET_WORDS)
      expect(seen, path).not.toContain(a.person.email)
      expect(seen, path).not.toContain('Ananya')
    }
  })
})

test.describe('accounts that have not hired', () => {
  test('a sign-up is not stored inside the first owner’s Ava', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const instanceA = await instanceOf(a)
    const b = await account(browser, 'Bella Tan') // signs up, never hires
    const [{ id: userB }] = await query<{ id: string }>('SELECT id FROM user_account WHERE email = $1', [b.person.email])

    // What the operator sees, with a reason, as "Ananya's Ava's people" and safety log.
    const ctx = await browser.newContext({ baseURL: BASE_URL })
    const page = await ctx.newPage()
    const headers = { ...OPERATOR, 'X-Operator-Reason': 'qa: checking owner A people', 'X-Operator-Instance': instanceA }
    const people = await (await page.request.get('/api/api/people', { headers })).json()
    const auditA = await (await page.request.get('/api/api/audit', { headers })).text()

    // Screenshot: the console's People tab for the first owner's instance.
    await page.goto(`/console?token=${ADMIN_TOKEN}`)
    await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'People', exact: true }).click()
    const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
    if (await open.isVisible()) {
      await page.getByLabel(/^Reason/).fill('qa: checking owner A people')
      await open.click()
    }
    await expect(page.getByText('Ananya Menon').first()).toBeVisible()
    await page.screenshot({ path: `${QA}/signup-in-first-instance-console-people.png`, fullPage: true })
    await ctx.close()

    const rows = await query<{ agent_id: string }>(
      'SELECT p.agent_id FROM person p JOIN user_account u ON u.person_id = p.id WHERE u.id = $1',
      [userB],
    )
    expect(rows.every((r) => r.agent_id !== instanceA), 'B’s Person lives in A’s instance').toBe(true)
    expect(JSON.stringify(people)).not.toContain('Bella Tan')
    expect(auditA).not.toContain(userB)
  })

  test('a second owner’s password and email changes are logged in their own Ava, not the first owner’s', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const [instanceA, instanceB] = [await instanceOf(a), await instanceOf(b)]
    const [{ id: userB }] = await query<{ id: string }>('SELECT id FROM user_account WHERE email = $1', [b.person.email])
    await b.api.ok(b.api.post('/auth/password', { current: b.person.password, new: 'another correct horse staple 2' }))
    await b.api.ok(b.api.post('/auth/email', { email: `new-${b.person.email}`, password: 'another correct horse staple 2' }))
    // The operator, reading the first owner's Safety log with a reason, sees nothing of B's account.
    const operator = { ...OPERATOR, 'X-Operator-Reason': 'qa: checking owner A safety log' }
    const ctx = await browser.newContext({ baseURL: BASE_URL })
    const auditA = await (await ctx.request.get('/api/api/audit', { headers: { ...operator, 'X-Operator-Instance': instanceA } })).text()
    const auditB = await (await ctx.request.get('/api/api/audit', { headers: { ...operator, 'X-Operator-Instance': instanceB } })).text()
    await ctx.close()
    expect(auditA).not.toContain(userB)
    expect(auditB).toContain('password_changed')
    expect(auditB).toContain('email_changed')
    const rows = await query<{ agent_id: string; action: string }>(
      "SELECT agent_id, action FROM audit_log WHERE action IN ('password_changed', 'email_changed') AND detail->>'user_id' = $1",
      [userB],
    )
    expect(rows).toHaveLength(2)
    expect(rows.filter((r) => r.agent_id === instanceA), 'B’s account events in A’s log').toHaveLength(0)
    expect(rows.every((r) => r.agent_id === instanceB)).toBe(true)
  })
})

test.describe('signed out', () => {
  test('every private API refuses, and private pages send you to log in', async ({ page, api }) => {
    void api
    for (const path of ['/app/me', '/app/notebook', '/app/people', '/app/messages', '/app/access-log', '/app/approvals',
      '/app/reminders', '/app/connections', '/app/providers', '/app/ask-link', '/app/ask-link/requests', '/app/channels/linked']) {
      expect((await page.request.get(`/api${path}`)).status(), path).toBe(401)
    }
    for (const path of ['/api/instances', '/api/overview', '/api/people', '/api/memories', '/api/spend']) {
      expect((await page.request.get(`/api${path}`)).status(), path).toBe(401)
    }
    for (const path of ['/api/docs', '/api/openapi.json', '/api/webhooks/telegram', '/api/p/..%2Fdocs', '/api/app/..%2F..%2Fopenapi.json']) {
      expect((await page.request.get(path)).status(), path).toBe(404)
    }
    for (const path of ['/dashboard', '/dashboard/settings', '/dashboard/notebook', '/meet']) {
      await page.goto(path)
      await expect(page, path).toHaveURL(new RegExp(`/login\\?next=${encodeURIComponent(path).replace(/%/g, '%')}`))
    }
  })

  test('a forged or missing session cookie gets nothing', async ({ browser, api }) => {
    void api
    await introduced(browser, 'Ananya Menon')
    const ctx = await browser.newContext({ baseURL: BASE_URL })
    await ctx.addCookies([{ name: 'superaide_session', value: 'forged-session-token', url: BASE_URL }])
    expect((await ctx.request.get('/api/app/notebook')).status()).toBe(401)
    expect((await ctx.request.get('/api/auth/me')).status()).toBe(401)
    await ctx.close()
  })
})

test.describe('operator break-glass', () => {
  test('a look at B’s Ava is recorded in B’s Access log only, and shows only B’s content', async ({ browser, page }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    await a.api.ok(a.api.post('/app/chat', { text: `Remember that ${SECRET_A}` }))
    const instanceB = await instanceOf(b)
    const r = await page.request.get('/api/api/memories', {
      headers: { ...OPERATOR, 'X-Operator-Reason': 'qa: a reported reminder for B', 'X-Operator-Instance': instanceB },
    })
    expect(r.ok()).toBeTruthy()
    expect(await r.text()).not.toContain(SECRET_WORDS)
    const logB = await b.api.ok<{ reason: string }[]>(b.api.get('/app/access-log'))
    const logA = await a.api.ok<unknown[]>(a.api.get('/app/access-log'))
    expect(logB.map((x) => x.reason)).toContain('qa: a reported reminder for B')
    expect(logA).toHaveLength(0)

    // B sees it in Settings → Access log.
    const pb = await b.context.newPage()
    await pb.goto('/dashboard/settings')
    await expect(pb.getByRole('list', { name: 'Access by Superaide staff' })).toContainText('qa: a reported reminder for B')
  })

  test('without a reason, or with an unknown instance, the console gets no content', async ({ page, browser }) => {
    await introduced(browser, 'Ananya Menon')
    expect((await page.request.get('/api/api/memories', { headers: OPERATOR })).status()).toBe(403)
    expect((await page.request.get('/api/api/memories', { headers: { ...OPERATOR, 'X-Operator-Reason': 'short' } })).status()).toBe(403)
    const bad = { ...OPERATOR, 'X-Operator-Reason': 'qa: an instance that does not exist' }
    expect((await page.request.get('/api/api/memories', { headers: { ...bad, 'X-Operator-Instance': crypto.randomUUID() } })).status()).toBe(404)
    expect((await page.request.get('/api/api/memories', { headers: { ...bad, 'X-Operator-Instance': 'not-a-uuid' } })).status()).toBe(404)
    expect((await page.request.get('/api/api/memories', { headers: { Authorization: 'Bearer wrong', 'X-Operator-Reason': 'qa: wrong token here' } })).status()).toBe(401)
  })
})

test.describe('Ask my Ava link', () => {
  test.skip(!ASK_LINK, 'ASK_LINK_ENABLED is off by default; set E2E_ASK_LINK=1 to run these')

  const H = { 'X-Requested-With': 'superaide-e2e' }
  const handle = (p: string) => `${p}-${Date.now().toString(36)}`

  async function publish(o: Owner, h: string): Promise<void> {
    await o.api.ok(o.context.request.put('/api/app/ask-link', { data: { handle: h, published: true }, headers: H }))
  }

  async function form(page: Page, h: string): Promise<string> {
    const r = await page.request.get(`/api/p/ask/${h}`)
    expect(r.ok(), `GET /p/ask/${h}`).toBeTruthy()
    return ((await r.json()) as { token: string }).token
  }

  function send(page: Page, h: string, data: Record<string, unknown>) {
    return page.request.post(`/api/p/ask/${h}`, {
      data: { name: 'Lee Wong', contact: 'lee@example.org', topic: 'Speaking slot', why: 'A talk', ...data },
      headers: H,
    })
  }

  async function personWait(): Promise<void> {
    await new Promise((r) => setTimeout(r, 1100)) // a person takes longer than ASK_MIN_SECONDS (1s in tests)
  }

  test('B cannot take A’s handle, in any case', async ({ browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const h = handle('qa-a')
    await publish(a, h)
    for (const wanted of [h, h.toUpperCase(), `  ${h}  `]) {
      const r = await b.context.request.put('/api/app/ask-link', { data: { handle: wanted, published: true }, headers: H })
      expect(r.status(), wanted).toBe(409)
    }
  })

  test('a request on A’s link reaches A only; B cannot dismiss, spam, reply to or approve it', async ({ browser, page }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const ha = handle('qa-a')
    const hb = handle('qa-b')
    await publish(a, ha)
    await publish(b, hb)
    const token = await form(page, ha)
    await personWait()
    expect((await send(page, ha, { token })).ok()).toBeTruthy()

    const reqA = await a.api.ok<{ requests: { id: string }[] }>(a.api.get('/app/ask-link/requests'))
    expect(reqA.requests).toHaveLength(1)
    const id = reqA.requests[0].id
    expect(JSON.stringify(await b.api.ok(b.api.get('/app/ask-link/requests?status=all')))).not.toContain('Lee Wong')
    const statsB = await b.api.ok<{ stats: Record<string, number> }>(b.api.get('/app/ask-link'))
    expect(statsB.stats.submissions).toBe(0)
    expect((await b.api.post(`/app/ask-link/requests/${id}/dismiss`)).status()).toBe(404)
    expect((await b.api.post(`/app/ask-link/requests/${id}/spam`)).status()).toBe(404)
    expect((await b.api.post(`/app/ask-link/requests/${id}/reply`, { text: 'hijack' })).status()).toBe(404)

    const drafted = await a.api.ok<{ reply: { approval_id: string } }>(
      a.api.post(`/app/ask-link/requests/${id}/reply`, { text: 'Thanks Lee, Friday works.' }),
    )
    expect((await b.api.post(`/app/approvals/${drafted.reply.approval_id}/approve`)).status()).toBe(404)
    expect(JSON.stringify(await b.api.ok(b.api.get('/app/approvals')))).not.toContain('Lee Wong')
    const pending = await a.api.ok<{ id: string; status: string }[]>(a.api.get('/app/approvals'))
    expect(pending.find((x) => x.id === drafted.reply.approval_id)?.status).toBe('pending')

    // B's dashboard shows no card from A's link.
    const pb = await b.context.newPage()
    await pb.goto('/dashboard')
    await expect(pb.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(pb.getByTestId('ask-request')).toHaveCount(0)
  })

  test('a form token from one link, a forged one, or none is refused', async ({ browser, page }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const b = await introduced(browser, 'Bella Tan')
    const ha = handle('qa-a')
    const hb = handle('qa-b')
    await publish(a, ha)
    await publish(b, hb)
    const tokenA = await form(page, ha)
    await personWait()
    const [v, issued, nonce, sig] = tokenA.split('.')
    for (const token of [
      tokenA, // A's form on B's link
      '',
      'garbage',
      `${v}.${issued}.${nonce}.${sig.slice(0, -2)}AA`,
      `${v}.${Number(issued) - 3 * 3600 * 1000}.${nonce}.${sig}`, // older than it says
      `${v}.${Date.now() + 3600_000}.${nonce}.${sig}`,
    ]) {
      const r = await send(page, hb, { token })
      expect(r.status(), `token ${token.slice(0, 20)}`).toBe(400)
      expect(await r.text()).toContain('Reload the page')
    }
    expect(await query('SELECT id FROM ask_request')).toHaveLength(0)
  })

  test('hostile input is refused or kept as text, and the honeypot stores nothing', async ({ browser, page }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const h = handle('qa-a')
    await publish(a, h)
    const token = await form(page, h)
    await personWait()
    // Too big, NUL characters, no CSRF header, a bad contact and an out-of-range deadline.
    expect((await send(page, h, { token, why: 'x'.repeat(9000) })).status()).toBe(413)
    expect((await page.request.post(`/api/p/ask/${h}`, { data: { name: 'Lee', contact: 'lee@example.org', topic: 'Talk', token } })).status()).toBe(403)
    expect((await send(page, h, { token, name: 'Lee\u0000' })).status()).toBe(422)
    expect((await send(page, h, { token, contact: 'not a contact' })).status()).toBe(422)
    expect((await send(page, h, { token, deadline: '2099-01-01' })).status()).toBe(422)
    // The honeypot says "sent" and keeps nothing.
    const trap = await send(page, h, { token, website: 'http://spam.example' })
    expect(trap.status()).toBe(200)
    expect(await query('SELECT id FROM ask_request')).toHaveLength(0)
    // Script and bidi tricks are stored as text, invisible characters stripped.
    const r = await send(page, h, { token, name: 'Lee‮gnow', topic: '<script>window.__pwned=1</script>' })
    expect(r.status()).toBe(200)
    const [row] = await query<{ name: string; topic: string }>('SELECT name, topic FROM ask_request')
    expect(row.name).toBe('Leegnow')
    expect(row.topic).toBe('<script>window.__pwned=1</script>')
  })

  test('unknown, odd and path-trick handles all get the ordinary 404 page', async ({ page, browser }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const h = handle('qa-a')
    await publish(a, h)
    for (const path of ['/a/nobody-has-this-one', '/a/..%2F..%2Fapi%2Fapp%2Fme', '/a/%00', `/a/${'x'.repeat(300)}`, '/a/admin']) {
      const r = await page.request.get(path)
      expect(r.status(), path).toBe(404)
      expect(await r.text(), path).not.toContain('Ananya')
    }
    for (const raw of ['nobody-has-this-one', '..%2F..%2Fapp%2Fme', 'x'.repeat(300)]) {
      expect((await page.request.get(`/api/p/ask/${raw}`)).status(), raw).toBe(404)
      expect((await page.request.post(`/api/p/ask/${raw}`, { data: { name: 'a', contact: 'a@b.co', topic: 'abc' }, headers: H })).status(), raw).toBe(404)
    }
    // The real handle works in another case, as people type it.
    expect((await page.request.get(`/api/p/ask/${h.toUpperCase()}`)).status()).toBe(200)
  })

  test('a visitor over the hourly limit gets a plain 429, and the owner never sees the extras', async ({ browser, page }) => {
    const a = await introduced(browser, 'Ananya Menon')
    const h = handle('qa-a')
    await publish(a, h)
    const token = await form(page, h)
    await personWait()
    for (let i = 0; i < 5; i++) expect((await send(page, h, { token, name: `Lee ${i}` })).status()).toBe(200)
    const over = await send(page, h, { token, name: 'Lee 6' })
    expect(over.status()).toBe(429)
    expect(await over.text()).toContain('Try again in an hour')
    const reqs = await a.api.ok<{ requests: unknown[] }>(a.api.get('/app/ask-link/requests'))
    expect(reqs.requests).toHaveLength(5)
  })
})
