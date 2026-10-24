// #292: a Google capability says so when Google is disconnected (it used to stay "Installed" with calendar actions on offer).
import { expect, test } from '../../src/fixtures'
import { plantSecret } from '../../src/helpers/vault'

test('Capabilities follows Disconnect Google on Connections', async ({ page, api, owner }, info) => {
  void owner
  await plantSecret('google', 'client_id', 'abc.apps.googleusercontent.com')
  await plantSecret('google', 'client_secret', 'GOCSPX-secretsecret')
  await plantSecret('google', 'refresh_token', 'rt-1234567890abcdef')
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const calendar = cards.find((c) => c.id === 'superaide/google-calendar')!
  await api.ok(api.post('/app/skills/superaide/google-calendar/install', { accepted: calendar.permissions.map((p) => p.id) }))

  const card = () => page.locator('li').filter({ hasText: 'Google Calendar' }).filter({ has: page.getByRole('button', { name: 'Remove' }) })
  await page.goto('/dashboard/capabilities')
  await expect(card()).toBeVisible()
  await expect(page.getByText("Google isn't connected")).toHaveCount(0)
  await expect(card().getByRole('button', { name: 'Allow', exact: true })).toBeVisible()

  await page.goto('/dashboard/connections')
  await page.getByRole('button', { name: /^Google Calendar/ }).click()
  await page.getByRole('button', { name: 'Disconnect Google Calendar' }).click()
  await expect(page.getByRole('button', { name: /^Google Calendar/ })).toContainText('Connect')

  await page.getByRole('link', { name: 'Capabilities', exact: true }).click()
  await expect(card().getByText("Google isn't connected, so this can't work yet.")).toBeVisible()
  await expect(card().getByRole('link', { name: 'Connect Google under Connections' })).toBeVisible()
  await expect(card().getByRole('button', { name: 'Allow', exact: true })).toHaveCount(0)
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-capabilities-${info.project.name}.png` })
})
