// #667: Google Drive is a capability with its own page: the connection (needs the owner's Google client), its limits as
// owner-controlled settings, and drafts that stay off until Drive is connected.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the Google Drive page shows what to connect first, saves a limit and keeps drafts behind the connection', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const drive = cards.find((c) => c.id === 'superaide/google-drive')!
  await api.ok(api.post('/app/skills/superaide/google-drive/install', { accepted: drive.permissions.map((p) => p.id) }))

  await page.goto('/dashboard/capabilities/drive')
  await expect(page.getByRole('heading', { name: 'Google Drive' }).first()).toBeVisible()
  await expect(page.getByRole('group', { name: 'Google Drive connection' })).toContainText('Add your Google OAuth client')
  await expect(page.getByText('Ava cannot delete, replace, move or share a file.')).toBeVisible() // the fixed rules, read-only

  await page.goto('/dashboard/capabilities/drive/limits')
  const group = page.getByRole('group', { name: 'How Ava uses your Drive' })
  await expect(group.getByLabel('Drive lookups per hour')).toHaveValue('30')
  await group.getByLabel('Drive lookups per hour').selectOption('10')
  await expect(group.getByText('Drive lookups per hour saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses your Drive' }).getByLabel('Drive lookups per hour')).toHaveValue('10')

  await page.goto('/dashboard/capabilities/drive/drafts')
  await expect(page.getByText("Google Drive isn't connected yet, so there is nothing to change here.")).toBeVisible()
  const axe = await new AxeBuilder({ page }).analyze()
  expect(axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/667-drive-${info.project.name}.png` })
})
