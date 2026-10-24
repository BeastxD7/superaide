// #433 at 390px: the Gmail reading settings fit a phone and nothing scrolls sideways.
import AxeBuilder from '@axe-core/playwright'
import { expect, query, test } from '../../src/fixtures'
import { plantSecret } from '../../src/helpers/vault'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })
const CLIENT = 'abc.apps.googleusercontent.com'

test('the Gmail reading settings fit 390px', async ({ page, api, owner }, info) => {
  void owner
  await plantSecret('google', 'client_id', CLIENT)
  await plantSecret('google', 'client_secret', 'GOCSPX-secretsecret')
  await plantSecret('google', 'gmail_refresh_token', 'rt-test-only-placeholder')
  await query(`UPDATE integration_secret SET meta = $1::jsonb WHERE name = 'gmail_refresh_token'`, [
    JSON.stringify({ scopes: ['https://www.googleapis.com/auth/gmail.readonly'], client_id: CLIENT, address: 'ananya@example.com' }),
  ])
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const gmail = cards.find((c) => c.id === 'superaide/gmail')!
  await api.ok(api.post('/app/skills/superaide/gmail/install', { accepted: gmail.permissions.map((p) => p.id) }))
  await page.goto('/dashboard/capabilities/gmail/reading') // the reading rules live under Gmail now (#441)
  const group = page.getByRole('group', { name: 'What Ava may read in Gmail' })
  await group.scrollIntoViewIfNeeded()
  await expect(group.getByLabel('Search size')).toBeVisible()
  await expectNoHorizontalScroll(page)
  const axe = await new AxeBuilder({ page }).include('#gmail-read').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await group.getByLabel('Search size').scrollIntoViewIfNeeded()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-390.png` })
  await group.getByLabel('One-time codes and password resets').selectOption('read')
  const dialog = page.getByRole('dialog', { name: 'Let Ava read one-time codes and password resets?' })
  await expect(dialog).toBeVisible()
  await expectNoHorizontalScroll(page)
  const box = await dialog.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)
  const dialogAxe = await new AxeBuilder({ page }).include('dialog').analyze()
  expect(dialogAxe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/451-dialog-390.png` })
})
