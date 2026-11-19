// The Promises page on a phone (390 wide): the form and a row fit the screen and nothing scrolls sideways.
import { expect, test } from '../../src/fixtures'

test.use({ viewport: { width: 390, height: 844 } })

test('the promises page fits a phone', async ({ page, api, owner }) => {
  void owner
  await api.ok(
    api.post('/app/commitments', {
      text: 'Reply to Meera about the long project timeline question she raised on Friday afternoon',
      who: 'Meera',
      due_at: new Date(Date.now() + 3600_000).toISOString(),
    }),
  )
  await page.goto('/dashboard/promises')
  const row = page.getByRole('listitem').filter({ hasText: 'Reply to Meera' })
  await expect(row.getByRole('button', { name: /Mark .* done/ })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'test-results/promises-390.png' })
})
