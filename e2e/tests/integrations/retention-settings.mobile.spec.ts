// #433: how much Ava reads of conversations and how long she keeps other people's messages are owner settings.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the retention settings fit 390px', async ({ page, owner }, info) => {
  void owner
  await page.goto('/dashboard/settings')
  const group = page.getByRole('group', { name: 'Reminders and approvals' })
  await group.scrollIntoViewIfNeeded()
  await expect(group.getByLabel('Close reminders that went off after')).toHaveValue('7')
  await group.getByLabel('Requests that wait for your yes expire after').selectOption('24')
  await expect(group.getByText('Requests that wait for your yes expire after saved.')).toBeVisible()
  await expectNoHorizontalScroll(page)
  await page.reload()
  await expect(page.getByRole('group', { name: 'Reminders and approvals' }).getByLabel('Requests that wait for your yes expire after')).toHaveValue('24')
  const axe = await new AxeBuilder({ page }).include('#retention').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await page.getByRole('group', { name: 'Reminders and approvals' }).scrollIntoViewIfNeeded()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-retention-${info.project.name}.png` })
})
