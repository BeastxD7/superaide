// The signature look controls and the live preview on a 390px phone: reachable, no sideways scroll.
import { expect, test } from '../../src/fixtures'

test.use({ viewport: { width: 390, height: 844 } })

test('the signature look fits a phone and the preview follows the controls', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/principal-card')
  const frame = page.frameLocator('[data-testid="signature-preview"]')
  await expect(frame.getByText('Verify this assistant')).toBeVisible()
  await page.getByLabel('Rose').check()
  await expect(frame.getByRole('link', { name: 'Verify this assistant' })).toHaveCSS('background-color', 'rgb(190, 18, 60)')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.getByTestId('signature-preview').scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/signature-settings-mobile.png' })
})
