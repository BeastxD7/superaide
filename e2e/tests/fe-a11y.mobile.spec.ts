// Phone layout (issue #78): nothing pushes the page sideways when a note, a name or a rule is one long
// unbroken string, and native inputs are 16px so iOS Safari does not zoom in on focus.
import { expect, test } from '../src/fixtures'
import { expectNoSidewaysScroll } from '../src/helpers/workspace'

test.use({ viewport: { width: 360, height: 740 } })

const LONG = 'https://www.linkedin.com/in/someone-with-a-really-long-profile-name-1234567890abcdefghijklmnopqrstuvwxyz'
const at = '2026-10-22T10:00:00Z'

test('a 100-character URL in a note, a name and a rule does not scroll the page sideways', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/notebook*', (r) =>
    r.fulfill({ json: { memories: [{ id: 'n1', text: LONG, category: 'about', source: 'chat', document: null, shareable: false, private: false, at }] } }),
  )
  await page.route('**/api/app/people', (r) =>
    r.fulfill({ json: { people: [{ id: 'p1', name: LONG.slice(8, 70), relationship: 'partner', vip: true, channels: [] }] } }),
  )
  await page.route('**/api/app/preferences', (r) =>
    r.fulfill({
      json: {
        urgent_topics: [LONG], digest: 'morning', interrupt_channel: 'app', quiet_start: '22:00', quiet_end: '07:00',
        vip_breaks_quiet: true, timezone: 'Asia/Singapore', shareable: [{ value: 'a', label: LONG }], never_share: LONG,
      },
    }),
  )
  for (const path of ['/dashboard/notebook', '/dashboard/people', '/dashboard/settings', '/dashboard']) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
    await page.waitForTimeout(400)
    await expectNoSidewaysScroll(page)
    // The page itself may not scroll because an inner area does: nothing visible may stick out either.
    const wide = await page.evaluate(() => {
      const width = document.documentElement.clientWidth
      return Array.from(document.querySelectorAll('main *'))
        .filter((el) => el.getBoundingClientRect().right > width + 1 && el.getBoundingClientRect().width > 0)
        .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)}`)
        .slice(0, 5)
    })
    expect(wide, path).toEqual([])
  }
})

test('native selects on the AI model page are at least 16px on a phone', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/providers', async (r) => {
    const real = await r.fetch()
    const body = (await real.json()) as Record<string, unknown>
    body.providers = [{ key: 'ollama', preset: 'ollama', base_url: 'http://127.0.0.1:11434/v1', enabled: true, key_hint: '', models: ['tiny'], checked_at: null }]
    await r.fulfill({ json: body })
  })
  await page.goto('/dashboard/models')
  await expect(page.getByRole('region', { name: 'Models' })).toBeVisible()
  const sizes = await page.evaluate(() => Array.from(document.querySelectorAll('main select, main input')).map((el) => parseFloat(getComputedStyle(el).fontSize)))
  expect(sizes.length).toBeGreaterThan(0)
  for (const size of sizes) expect(size).toBeGreaterThanOrEqual(16)
})
