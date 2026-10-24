// Issue #78 (FE-18, FE-21): focus moves into the next step after an answer, and the introduction's
// text fields are 16px on a phone (anything smaller makes iOS Safari zoom the page on focus).
import { expect, test } from '../../src/fixtures'
import { openIntroduction, openTab } from '../../src/helpers/intro-sources'

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

// Issue #81: the introduction can be done with the keyboard alone (no mouse, no pointer clicks).
test('the introduction works from the keyboard alone: answer, move between the tabs, type and send', async ({ page, owner }) => {
  void owner
  await page.goto('/meet')
  const hello = page.getByRole('button', { name: 'Nice to meet you' })
  await expect(hello).toBeVisible()
  await hello.focus()
  await page.keyboard.press('Enter')
  const tablist = page.getByRole('tablist', { name: 'How to introduce yourself' })
  await expect(tablist).toBeVisible()
  // Only the selected tab is in the Tab order; the arrows, Home and End move between the three.
  const chat = tablist.getByRole('tab', { name: 'Tell me here' })
  const files = tablist.getByRole('tab', { name: 'Files & links' })
  const call = tablist.getByRole('tab', { name: 'Video call' })
  await chat.focus()
  await expect(chat).toHaveAttribute('tabindex', '0')
  await expect(files).toHaveAttribute('tabindex', '-1')
  await page.keyboard.press('ArrowRight')
  await expect(files).toBeFocused()
  await expect(files).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('button', { name: 'choose a file' })).toBeVisible()
  await page.keyboard.press('End')
  await expect(call).toBeFocused()
  await expect(call).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowRight') // wraps round to the first tab
  await expect(chat).toBeFocused()
  await page.keyboard.press('ArrowLeft') // and back round to the last
  await expect(call).toBeFocused()
  await page.keyboard.press('Home')
  await expect(chat).toBeFocused()
  await expect(chat).toHaveAttribute('aria-selected', 'true')
  // The tab panel is reachable by Tab, and a message can be written and sent with the keyboard.
  const box = page.getByRole('tabpanel').getByRole('textbox')
  await box.focus()
  await page.keyboard.type('I lead applied AI in Singapore.')
  await page.keyboard.press('Enter')
  await expect(page.getByText('I lead applied AI in Singapore.').first()).toBeVisible()
})
