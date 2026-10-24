// The sign-off tick on a 390px phone: reachable, fits the screen, no sideways scroll.
import { expect, test } from '../../src/fixtures'
import { queueEmail } from './_sign-helper'

test.use({ viewport: { width: 390, height: 844 } })

test('the tick fits a phone and can be changed', async ({ page, owner }) => {
  void owner
  await queueEmail()
  await page.goto('/dashboard')
  const box = page.getByLabel('Sign as Ava, personal assistant of Jordan')
  await expect(box).toBeChecked()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: 'test-results/approval-sign-mobile.png' })
  await box.uncheck()
  await expect(box).not.toBeChecked()
})
