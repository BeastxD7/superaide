// The chat at 390px: the empty state fits and the composer is in view; with messages the transcript has the whole height.
import { expect, test } from '../../src/fixtures'
import { composer, expectNoSidewaysScroll, openChat, openEmptyChat } from '../../src/helpers/workspace'

test.use({ viewport: { width: 390, height: 780 } })

test('empty: greeting and four equal quick actions fit 390px, composer in view, no sideways scroll', async ({ page, owner }) => {
  void owner
  await openEmptyChat(page)
  const empty = page.getByTestId('chat-empty')
  await expect(empty.getByText(/^Hello, \w+\. What shall we work on\?$/)).toBeInViewport()
  const buttons = empty.getByRole('button')
  await expect(buttons).toHaveCount(4)
  const widths = await buttons.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().width)))
  expect(new Set(widths).size, 'equal-width chips').toBe(1)
  for (let i = 0; i < 4; i++) await expect(buttons.nth(i)).toBeInViewport()
  await expect(composer(page)).toBeInViewport()
  await expectNoSidewaysScroll(page)
})

test('with messages: no greeting or chips, the transcript fills the space, composer in view', async ({ page, owner }) => {
  void owner
  await openChat(page)
  await expect(page.getByTestId('chat-empty')).toHaveCount(0)
  await expect(composer(page)).toBeInViewport()
  await expectNoSidewaysScroll(page)
  const gap = await page.getByRole('main').evaluate(() => {
    const bar = document.querySelector('header')!
    const scroller = Array.from(document.querySelectorAll('main *')).find((el) => getComputedStyle(el).overflowY === 'auto')
    return scroller ? scroller.getBoundingClientRect().top - bar.getBoundingClientRect().bottom : -1
  })
  expect(gap).toBeGreaterThanOrEqual(0)
  expect(gap).toBeLessThan(10)
})
