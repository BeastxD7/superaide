// #669: Linear is a capability with its own page: connect with an API key (checked before it is saved), limits as
// owner-controlled settings, fixed rules shown read-only.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the Linear page asks for an API key, refuses a malformed one, and saves a limit', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const linear = cards.find((c) => c.id === 'superaide/linear')!
  await api.ok(api.post('/app/skills/superaide/linear/install', { accepted: linear.permissions.map((p) => p.id) }))

  await page.goto('/dashboard/capabilities/linear')
  await expect(page.getByRole('heading', { name: 'Linear' }).first()).toBeVisible()
  await expect(page.getByText('Ava cannot edit, close, delete, assign or move anything.')).toBeVisible()
  const group = page.getByRole('group', { name: 'Linear connection' })
  await group.getByLabel('Linear API key').fill('not a token')
  await group.getByRole('button', { name: 'Connect Linear' }).click()
  await expect(group.getByText("That doesn't look like a Linear API key.")).toBeVisible()
  await expect(group.getByLabel('Linear API key')).toBeVisible() // still not connected

  await page.goto('/dashboard/capabilities/linear/limits')
  const limits = page.getByRole('group', { name: 'How Ava uses your Linear' })
  await expect(limits.getByLabel('Linear lookups per hour')).toHaveValue('30')
  await limits.getByLabel('Linear lookups per hour').selectOption('10')
  await expect(limits.getByText('Linear lookups per hour saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses your Linear' }).getByLabel('Linear lookups per hour')).toHaveValue('10')

  const axe = await new AxeBuilder({ page }).analyze()
  expect(axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-linear-${info.project.name}.png` })
})
