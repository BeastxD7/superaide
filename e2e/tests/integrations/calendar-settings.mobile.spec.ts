// #433: what Ava may read of the calendar is a visible, owner-controlled setting on the Calendar card.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Calendar settings fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const cal = cards.find((c) => c.id === 'superaide/google-calendar')!
  await api.ok(api.post('/app/skills/superaide/google-calendar/install', { accepted: cal.permissions.map((p) => p.id) }))
  await page.goto('/dashboard/capabilities')
  const group = page.getByRole('group', { name: 'What Ava may read in your calendar' })
  await group.scrollIntoViewIfNeeded()
  for (const label of ['How far ahead Ava reads', 'Your day starts at', 'Your day ends at', 'Events she learns from per read'])
    await expect(group.getByText(label, { exact: true })).toBeVisible()
  await expect(group.getByLabel('How far ahead Ava reads')).toHaveValue('14')
  await group.getByLabel('How far ahead Ava reads').selectOption('7')
  await expect(group.getByText('How far ahead Ava reads saved.')).toBeVisible()
  await expectNoHorizontalScroll(page)
  await page.reload()
  await expect(page.getByRole('group', { name: 'What Ava may read in your calendar' }).getByLabel('How far ahead Ava reads')).toHaveValue('7')
  const axe = await new AxeBuilder({ page }).include('#calendar').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await page.getByRole('group', { name: 'What Ava may read in your calendar' }).scrollIntoViewIfNeeded()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-cal-${info.project.name}.png` })
})
