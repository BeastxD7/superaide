// #433: how much Ava reads of conversations and how long she keeps other people's messages are owner settings.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('Connections shows the reading and keeping rules and saves a change', async ({ page, owner }, info) => {
  void owner
  await page.goto('/dashboard/connections')
  const group = page.getByRole('group', { name: 'Reading and keeping' })
  await group.scrollIntoViewIfNeeded()
  for (const label of ['Slack thread depth', 'Context from your other channels', "Keep other people's messages for"])
    await expect(group.getByText(label, { exact: true })).toBeVisible()
  await expect(group.getByLabel('Slack thread depth')).toHaveValue('25')
  await expect(group.getByLabel("Keep other people's messages for")).toHaveValue('0')
  await group.getByLabel("Keep other people's messages for").selectOption('90')
  await expect(group.getByText("Keep other people's messages for saved.")).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'Reading and keeping' }).getByLabel("Keep other people's messages for")).toHaveValue('90')
  const axe = await new AxeBuilder({ page }).include('#channels').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await page.getByRole('group', { name: 'Reading and keeping' }).scrollIntoViewIfNeeded()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-channels-${info.project.name}.png` })
})
