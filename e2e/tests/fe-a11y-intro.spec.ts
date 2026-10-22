// Issue #78 (FE-18, FE-21): focus moves into the next step after an answer, and the introduction's
// text fields are 16px on a phone (anything smaller makes iOS Safari zoom the page on focus).
import { expect, test } from '../src/fixtures'
import { openIntroduction, openTab } from '../src/helpers/intro-sources'

test.use({ ownerState: 'hired' })

test('after an answer, keyboard focus moves into the next step instead of being lost on the page', async ({ page, owner }) => {
  void owner
  await page.goto('/meet')
  const hello = page.getByRole('button', { name: 'Nice to meet you' })
  await hello.click()
  await expect(page.getByRole('tablist', { name: 'How to introduce yourself' })).toBeVisible()
  await expect
    .poll(() => page.evaluate(() => Boolean(document.activeElement && document.activeElement !== document.body)), { timeout: 5_000 })
    .toBe(true)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 800 } })
  test('the introduction link and chat boxes use a 16px font', async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
    await openTab(page, 'Files & links')
    const size = (name: string) => page.getByLabel(name).evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
    expect(await size('Link to a page about you')).toBeGreaterThanOrEqual(16)
    await openTab(page, 'Tell me here')
    expect(await page.getByRole('textbox').first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16)
  })
})
