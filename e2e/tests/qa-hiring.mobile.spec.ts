// QA pass on hiring, on a phone (Pixel 7): no sideways scrolling, and the hire shortcuts are on screen.
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { HirePage, Meet } from '../src/helpers/introduction'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/hiring`
test.use({ contextOptions: { reducedMotion: 'reduce' } })

async function sideways(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
}

test.describe('an account that has not hired yet', () => {
  test.use({ ownerState: 'account' })

  test('the marketplace hero shows "Hire your own Ava" on screen, with no sideways scroll', async ({ page, owner }) => {
    void owner
    await page.goto('/')
    const hire = page.getByRole('button', { name: 'Hire your own Ava' })
    await expect(hire).toBeInViewport()
    await expect(page.getByRole('button', { name: 'Meet the agents' })).toBeVisible()
    await page.screenshot({ path: `${QA}/mobile-marketplace-account.png`, fullPage: true })
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    await hire.click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
  })

  test('the profile and the hire page fit the screen', async ({ page, owner }) => {
    void owner
    await page.goto('/agents/ava')
    await expect(page.getByRole('button', { name: 'Hire Ava' })).toBeVisible()
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    const hire = new HirePage(page)
    await hire.open()
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    await hire.agree().check()
    await hire.hireButton().click()
    await expect(hire.sayHello()).toBeInViewport()
    expect(await sideways(page)).toBeLessThanOrEqual(0)
  })
})

test.describe('hired', () => {
  test.use({ ownerState: 'hired' })

  test('the introduction and its finished state fit the screen; Start working is reachable', async ({ page, api, owner }) => {
    void owner
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Hello')
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    await api.answer('reach', { quiet_start: '22:00', quiet_end: '07:00' })
    await api.answer('share', { shareable: [] })
    await api.answer('rules')
    await api.answer('connect', { choice: 'done' })
    await page.reload()
    await meet.expectDone()
    await expect(meet.startWorking()).toBeInViewport()
    expect(await sideways(page)).toBeLessThanOrEqual(0)
    await meet.startWorking().click()
    await expect(page).toHaveURL(/\/dashboard$/)
  })
})

test('the "Infant to Adult" band is shown in full on a phone, not cut off at the edge', async ({ page, api }) => {
  void api
  await page.goto('/')
  const band = page.getByText('Infant to Adult', { exact: true })
  await band.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${QA}/mobile-infant-to-adult-cut.png` })
  // The words must fit inside the band that clips them.
  const overflow = await band.evaluate((el) => {
    const box = el.parentElement!.getBoundingClientRect()
    return Math.max(el.scrollWidth - el.clientWidth, 0) + Math.max(el.getBoundingClientRect().right - box.right, 0)
  })
  expect(overflow).toBeLessThanOrEqual(1)
})
