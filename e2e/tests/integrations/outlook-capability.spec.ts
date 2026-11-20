// #668: Outlook mail is a capability with its own page: save your own Microsoft app (checked before it is saved), limits as
// owner-controlled settings, fixed rules shown read-only.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the Outlook page asks for the Microsoft app, refuses a malformed id, and saves a limit', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const outlook = cards.find((c) => c.id === 'superaide/outlook-mail')!
  await api.ok(api.post('/app/skills/superaide/outlook-mail/install', { accepted: outlook.permissions.map((p) => p.id) }))

  await page.goto('/dashboard/capabilities/outlook')
  await expect(page.getByRole('heading', { name: 'Outlook mail' }).first()).toBeVisible()
  await expect(page.getByText('Ava cannot delete, move, forward or edit mail.')).toBeVisible()
  const group = page.getByRole('group', { name: 'Outlook connection' })
  await group.getByLabel('Application (client) ID').fill('not-a-guid')
  await group.getByLabel('Client secret value').fill('abcdefghijkl1234')
  await group.getByRole('button', { name: 'Save Microsoft app' }).click()
  await expect(group.getByText(/The application \(client\) id looks like/)).toBeVisible()
  await expect(group.getByLabel('Application (client) ID')).toBeVisible() // still not saved

  await group.getByLabel('Application (client) ID').fill('11111111-2222-3333-4444-555555555555')
  await group.getByRole('button', { name: 'Save Microsoft app' }).click()
  await expect(group.getByRole('button', { name: 'Connect Outlook' })).toBeVisible()

  await page.goto('/dashboard/capabilities/outlook/limits')
  const limits = page.getByRole('group', { name: 'How Ava uses your Outlook' })
  await expect(limits.getByLabel('Outlook lookups per hour')).toHaveValue('30')
  await limits.getByLabel('Outlook lookups per hour').selectOption('10')
  await expect(limits.getByText('Outlook lookups per hour saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses your Outlook' }).getByLabel('Outlook lookups per hour')).toHaveValue('10')

  const axe = await new AxeBuilder({ page }).analyze()
  expect(axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/668-outlook-${info.project.name}.png` })
})
