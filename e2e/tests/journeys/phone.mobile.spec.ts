// The same first steps on a phone: everything fits, nothing scrolls sideways.
import { expect, newPerson, test } from '../../src/fixtures'
import type { Page } from '@playwright/test'

async function noSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow, 'page scrolls sideways').toBeLessThanOrEqual(1)
}

test('on a phone, someone signs up, hires Ava and starts the introduction', async ({ page, api }) => {
  void api
  const me = newPerson('Ananya Menon')
  await page.goto('/agents/ava')
  await noSidewaysScroll(page)
  await page.screenshot({ path: 'test-results/journey/phone-profile.png', fullPage: true })
  await page.getByRole('button', { name: 'Hire Ava' }).click()
  await page.getByLabel('Your name').fill(me.name)
  await page.getByLabel('Email').fill(me.email)
  await page.getByLabel('Password', { exact: true }).fill(me.password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Hire Ava' }).click()
  await page.getByRole('button', { name: /Say hello/ }).click()
  await page.getByRole('button', { name: 'Nice to meet you' }).click({ timeout: 15_000 })
  await expect(page.getByRole('tab', { name: 'Tell me here' })).toBeVisible({ timeout: 15_000 })
  await noSidewaysScroll(page)
  await page.screenshot({ path: 'test-results/journey/phone-meet.png' })
})
