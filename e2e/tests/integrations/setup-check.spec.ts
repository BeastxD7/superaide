// #567: "Check my setup" on the Connections page runs the in-app doctor checks and shows cause and fix per row.
import { expect, test } from '../../src/fixtures'

test('the owner checks their setup from the Connections page', async ({ page, owner }, info) => {
  void owner
  await page.goto('/dashboard/connections')
  const card = page.getByTestId('setup-check')
  await expect(card.getByText('Check my setup').first()).toBeVisible()
  await expect(page.getByTestId('setup-result')).toHaveCount(0)
  await card.getByRole('button', { name: 'Check my setup' }).click()
  await expect(page.getByTestId('setup-result').first()).toBeVisible()
  await expect(card).toContainText('Spend cap')
  await expect(card.getByRole('button', { name: 'Check again' })).toBeEnabled()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/567-setup-check-${info.project.name}.png`, fullPage: true })
})

test('a signed-out visitor cannot run the check', async ({ request }) => {
  const r = await request.post('/api/app/setup-check', { headers: { 'X-Requested-With': 'superaide' } })
  expect(r.status()).toBe(401)
})
