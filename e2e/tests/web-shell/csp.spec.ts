// #80: a nonce-based Content-Security-Policy. Scripts and style elements need this request's random nonce; the only inline
// exception is style="" attributes (style-src-attr), which can style but never run script.
import { expect, test } from '../../src/fixtures'

test('the policy has a fresh nonce per request and no unsafe-inline for scripts or style elements', async ({ page }) => {
  const nonces: string[] = []
  for (const path of ['/', '/login', '/console']) {
    const res = await page.request.get(path)
    const csp = res.headers()['content-security-policy'] ?? ''
    const script = csp.split('; ').find((d) => d.startsWith('script-src ')) ?? ''
    const style = csp.split('; ').find((d) => d.startsWith('style-src ')) ?? ''
    expect(script, path).toMatch(/'nonce-[A-Za-z0-9+/=]+'/)
    expect(script, path).toContain("'strict-dynamic'")
    expect(script, path).not.toContain("'unsafe-inline'")
    expect(style, path).toMatch(/'nonce-/)
    expect(style, path).not.toContain("'unsafe-inline'")
    expect(csp, path).toContain("style-src-attr 'unsafe-inline'")
    nonces.push(script.match(/'nonce-([^']+)'/)![1])
    // The page's own scripts carry the same nonce, so they run; Next stamped it on them.
    const html = await res.text()
    const stamped = [...html.matchAll(/<script[^>]*\snonce="([^"]+)"/g)].map((m) => m[1])
    expect(stamped.length, `${path} has inline scripts with a nonce`).toBeGreaterThan(0)
    expect(new Set(stamped), path).toEqual(new Set([nonces.at(-1)]))
  }
  expect(new Set(nonces).size, 'a different nonce on every request').toBe(nonces.length)
})

test('injected markup with an inline handler or an inline script does not run', async ({ page }) => {
  await page.goto('/login')
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('beforeend', '<img src="data:image/gif;base64,x" onerror="window.__injected=true"><div id="x" onclick="window.__injected=true"></div>')
    document.getElementById('x')!.click()
  })
  await page.waitForTimeout(300)
  expect(await page.evaluate(() => (window as unknown as { __injected?: boolean }).__injected ?? false)).toBe(false)
})
