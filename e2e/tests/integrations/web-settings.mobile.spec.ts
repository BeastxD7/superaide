// #433: what Ava may read of the calendar is a visible, owner-controlled setting on the Calendar card.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Web research settings fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const cal = cards.find((c) => c.id === 'superaide/web-research')!
  await api.ok(api.post('/app/skills/superaide/web-research/install', { accepted: cal.permissions.map((p) => p.id) }))
  await page.goto('/dashboard/capabilities')
  const group = page.getByRole('group', { name: 'How Ava uses the web' })
  await group.scrollIntoViewIfNeeded()
  for (const label of ['Searches per step', 'Pages she may open per step', 'When Ava searches the web'])
    await expect(group.getByText(label, { exact: true })).toBeVisible()
  await expect(group.getByLabel('Searches per step')).toHaveValue('3')
  await group.getByLabel('Searches per step').selectOption('1')
  await expect(group.getByText('Searches per step saved.')).toBeVisible()
  await expectNoHorizontalScroll(page)
  await page.reload()
  await expect(page.getByRole('group', { name: 'How Ava uses the web' }).getByLabel('Searches per step')).toHaveValue('1')
  const axe = await new AxeBuilder({ page }).include('#web').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await page.getByRole('group', { name: 'How Ava uses the web' }).scrollIntoViewIfNeeded()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-web-${info.project.name}.png` })
})
