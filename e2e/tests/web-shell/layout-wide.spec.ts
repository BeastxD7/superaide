// #663: pages use the room they are given. On a wide screen the content is wide and in several columns where it holds
// cards; a page of prose keeps a comfortable line length; and nothing sticks out sideways at phone width.
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'

const pageBox = (page: Page) => page.locator('[data-page-container] > div').first().boundingBox()
const insetBox = (page: Page) => page.locator('[data-page-container]').first().boundingBox()

// The app scrolls inside the window, so the document itself never overflows; look at the page's own scroller.
async function sideways(page: Page): Promise<number> {
  return page.evaluate(() => {
    const el = document.querySelector('[data-page-container]')
    return el ? el.scrollWidth - el.clientWidth : -1
  })
}

async function seedReminders(api: { post(p: string, d?: unknown): Promise<{ ok(): boolean }> }, n: number) {
  for (let i = 1; i <= n; i++) {
    const r = await api.post('/app/reminders', { text: `Call the supplier number ${i} tomorrow at 10am` })
    expect(r.ok()).toBeTruthy()
  }
}

for (const [w, h, minShare] of [
  [1440, 900, 0.7],
  [1920, 1080, 0.6],
] as const) {
  test(`reminders use the width at ${w}: wide content, cards in columns`, async ({ page, api, owner }) => {
    void owner
    await seedReminders(api, 4)
    await page.setViewportSize({ width: w, height: h })
    await page.goto('/dashboard/reminders')
    const cards = page.getByRole('list', { name: 'Upcoming reminders' }).getByRole('listitem')
    await expect(cards).toHaveCount(4)
    const inset = (await insetBox(page))!
    const content = (await pageBox(page))!
    expect(content.width / inset.width).toBeGreaterThan(minShare) // not a narrow centred column
    const xs = new Set(await cards.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left))))
    expect(xs.size).toBeGreaterThanOrEqual(2) // at least two columns
    expect(await sideways(page)).toBeLessThanOrEqual(0)
  })
}

test('on a very large monitor the page stops growing at its cap and stays centred', async ({ page, owner }) => {
  void owner
  await page.setViewportSize({ width: 2560, height: 1300 })
  await page.goto('/dashboard/reminders')
  await expect(page.getByRole('heading', { level: 1, name: 'Reminders' })).toBeVisible()
  const content = (await pageBox(page))!
  const inset = (await insetBox(page))!
  expect(content.width).toBeLessThanOrEqual(1152) // the `list` width is 72rem
  const left = content.x - inset.x
  const right = inset.x + inset.width - (content.x + content.width)
  expect(Math.abs(left - right)).toBeLessThan(2)
})

test('a page of prose keeps a comfortable measure on a wide screen', async ({ page, owner }) => {
  void owner
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.goto('/dashboard/reminders')
  const lead = page.locator('[data-page-header] p').first()
  await expect(lead).toBeVisible()
  const box = (await lead.boundingBox())!
  expect(box.width).toBeLessThanOrEqual(800) // 70ch, never the full page
})

test('reminders at 360 are one column and nothing sticks out sideways', async ({ page, api, owner }) => {
  void owner
  await seedReminders(api, 3)
  await page.setViewportSize({ width: 360, height: 740 })
  await page.goto('/dashboard/reminders')
  const cards = page.getByRole('list', { name: 'Upcoming reminders' }).getByRole('listitem')
  await expect(cards).toHaveCount(3)
  const xs = new Set(await cards.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left))))
  expect(xs.size).toBe(1)
  expect(await sideways(page)).toBeLessThanOrEqual(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
})

test('reading order is the visual order: from a card, Shift+Tab goes back to the add form', async ({ page, api, owner }) => {
  void owner
  await seedReminders(api, 2)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/dashboard/reminders')
  const first = page.getByRole('list', { name: 'Upcoming reminders' }).getByRole('listitem').first().getByRole('button').first()
  await first.focus()
  await expect(first).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(page.getByLabel('New reminder')).toBeFocused() // the disabled Add button is skipped
})
