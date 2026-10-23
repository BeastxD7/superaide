// The Settings notifications card exists only in a build with NEXT_PUBLIC_PWA=1 (ADR 0011); a normal build never shows it.
//   NEXT_PUBLIC_PWA=1 npm run build (in frontend/), then E2E_PWA=1 E2E_SLOT=41 npx playwright test settings-notifications
import { expect, test } from '../src/fixtures'

const ON = process.env.E2E_PWA === '1'

test('without the installable-app flag there is no notifications card', async ({ page, owner }) => {
  test.skip(ON, 'this build has the flag on')
  void owner
  await page.goto('/dashboard/settings')
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByText('Notifications', { exact: true })).toHaveCount(0)
})

test('with the flag on, Settings offers to turn on notifications', async ({ page, owner }, info) => {
  test.skip(!ON, 'build with NEXT_PUBLIC_PWA=1 and set E2E_PWA=1')
  void owner
  await page.goto('/dashboard/settings')
  const card = page.locator('[data-slot="card"]').filter({ hasText: 'Notifications' })
  await expect(card).toBeVisible()
  await expect(card.getByRole('button', { name: 'Turn on notifications' }).or(card.getByText(/can.t show notifications|are on/))).toBeVisible()
  if (process.env.SHOT_DIR) await card.screenshot({ path: `${process.env.SHOT_DIR}/after-notifications-${info.project.name}.png` })
})
