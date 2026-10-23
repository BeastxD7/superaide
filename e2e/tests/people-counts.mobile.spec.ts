// On a phone, People still says where each person is recognised (#300).
import { expect, test } from '../src/fixtures'

test('the People page shows "Recognised on" for each person on a phone', async ({ page, api, owner }) => {
  void owner
  void api
  await page.goto('/dashboard/people')
  const priya = page.getByRole('row').filter({ hasText: 'Priya' })
  await expect(priya.getByText('Recognised on:')).toBeVisible()
  await expect(priya.getByText('Not linked yet').locator('visible=true')).toBeVisible()
  // No horizontal scroll from adding it.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
