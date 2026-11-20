// #669: GitHub is a capability with its own page: connect with an access token (checked before it is saved), limits as
// owner-controlled settings, fixed rules shown read-only.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the GitHub page asks for a token, refuses a malformed one, and saves a limit', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const github = cards.find((c) => c.id === 'superaide/github')!
  await api.ok(api.post('/app/skills/superaide/github/install', { accepted: github.permissions.map((p) => p.id) }))

  await page.goto('/dashboard/capabilities/github')
  await expect(page.getByRole('heading', { name: 'GitHub' }).first()).toBeVisible()
  await expect(page.getByText('Ava cannot edit, close, delete, merge or assign anything.')).toBeVisible()
  const group = page.getByRole('group', { name: 'GitHub connection' })
  await group.getByLabel('GitHub access token').fill('not a token')
  await group.getByRole('button', { name: 'Connect GitHub' }).click()
  await expect(group.getByText("That doesn't look like a GitHub access token.")).toBeVisible()
  await expect(group.getByLabel('GitHub access token')).toBeVisible() // still not connected

  await page.goto('/dashboard/capabilities/github/limits')
  const limits = page.getByRole('group', { name: 'How Ava uses your GitHub' })
  await expect(limits.getByLabel('GitHub lookups per hour')).toHaveValue('30')
  await limits.getByLabel('GitHub lookups per hour').selectOption('10')
  await expect(limits.getByText('GitHub lookups per hour saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses your GitHub' }).getByLabel('GitHub lookups per hour')).toHaveValue('10')

  const axe = await new AxeBuilder({ page }).analyze()
  expect(axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-github-${info.project.name}.png` })
})
