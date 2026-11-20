// #669: Notion is a capability with its own page: connect with an integration secret (checked before it is saved), limits as
// owner-controlled settings, fixed rules shown read-only.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the Notion page asks for an integration secret, refuses a malformed one, and saves a limit', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const notion = cards.find((c) => c.id === 'superaide/notion')!
  await api.ok(api.post('/app/skills/superaide/notion/install', { accepted: notion.permissions.map((p) => p.id) }))

  await page.goto('/dashboard/capabilities/notion')
  await expect(page.getByRole('heading', { name: 'Notion' }).first()).toBeVisible()
  await expect(page.getByText('Ava cannot edit, archive, delete or move anything.')).toBeVisible()
  const group = page.getByRole('group', { name: 'Notion connection' })
  await group.getByLabel('Notion integration secret').fill('not a token')
  await group.getByRole('button', { name: 'Connect Notion' }).click()
  await expect(group.getByText("That doesn't look like a Notion integration secret.")).toBeVisible()
  await expect(group.getByLabel('Notion integration secret')).toBeVisible() // still not connected

  await page.goto('/dashboard/capabilities/notion/limits')
  const limits = page.getByRole('group', { name: 'How Ava uses your Notion' })
  await expect(limits.getByLabel('Notion lookups per hour')).toHaveValue('30')
  await limits.getByLabel('Notion lookups per hour').selectOption('10')
  await expect(limits.getByText('Notion lookups per hour saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses your Notion' }).getByLabel('Notion lookups per hour')).toHaveValue('10')

  const axe = await new AxeBuilder({ page }).analyze()
  expect(axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-notion-${info.project.name}.png` })
})
