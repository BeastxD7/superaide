// #663: the operator console uses the room it has. Wide: the content fills the page and cards sit in as many columns as
// fit; beside an open sidebar at 768 the cards stack instead of squashing their text; nothing goes sideways at 360.
import { expect, test } from '../../src/fixtures'
import { ADMIN_TOKEN } from '../../src/env'

async function open(page: import('@playwright/test').Page, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByText('What is live right now')).toBeVisible()
}

const sideways = (page: import('@playwright/test').Page) =>
  page.evaluate(() => {
    const main = document.querySelector('main.c-main')
    return Math.max(document.documentElement.scrollWidth - document.documentElement.clientWidth, main ? main.scrollWidth - main.clientWidth : 0)
  })

const lefts = (page: import('@playwright/test').Page, selector: string) =>
  page.locator(selector).evaluateAll((els) => [...new Set(els.map((e) => Math.round(e.getBoundingClientRect().left)))].length)

test('at 1920 the overview fills the page and its tiles sit in several columns', async ({ page, owner }) => {
  void owner
  await open(page, 1920, 1080)
  const box = (await page.locator('main.c-main').boundingBox())!
  const first = (await page.locator('.c-card').first().boundingBox())!
  expect(first.width / box.width).toBeGreaterThan(0.85) // not a 1152px column in the middle
  expect(await lefts(page, '.c-tile')).toBeGreaterThanOrEqual(3)
  expect(await sideways(page)).toBeLessThanOrEqual(0)
})

test('at 768, beside the open sidebar, tiles stack in one column and nothing goes sideways', async ({ page, owner }) => {
  void owner
  await open(page, 768, 1024)
  expect(await sideways(page)).toBeLessThanOrEqual(0)
  const tiles = page.locator('.c-grid .c-tile')
  const widths = await tiles.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)))
  expect(Math.min(...widths)).toBeGreaterThan(240) // text is never squeezed into a one-word column
})

test('at 360 nothing goes sideways on any monitor page', async ({ page, owner }) => {
  void owner
  await open(page, 360, 740)
  for (const tab of ['Overview', 'Spend', 'Latency', 'Approvals']) {
    if (tab !== 'Overview') {
      await page.getByRole('button', { name: /toggle sidebar/i }).first().click()
      await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: tab, exact: true }).click()
      await page.keyboard.press('Escape')
    }
    await page.waitForTimeout(500)
    expect(await sideways(page), tab).toBeLessThanOrEqual(0)
  }
})
