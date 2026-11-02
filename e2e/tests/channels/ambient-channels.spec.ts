// #509: reading a whole channel is the owner's opt-in, one channel at a time, off by default; turning one on asks first.
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { plantMissingScopes, plantSlackChannel, storedRoomMessages } from '../../src/helpers/vault'

const shot = async (page: Page, name: string, project: string) => {
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}-${project}.png` })
}
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)

test('every channel starts off, and turning one on asks first', async ({ page, owner }, info) => {
  void owner
  await plantSlackChannel('C0TEAM01', 'team')
  await page.goto('/dashboard/connections')
  const group = page.getByRole('group', { name: 'Reading whole channels' })
  await group.scrollIntoViewIfNeeded()
  const toggle = group.getByRole('switch', { name: '#team' })
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await expect(group.getByText('Off (default)')).toBeVisible()
  await expect(group.getByRole('region', { name: 'How reading whole channels works' })).toBeVisible()

  await toggle.click()
  const dialog = page.getByRole('dialog', { name: /Let Ava read all of #team\?/ })
  await expect(dialog).toBeVisible()
  const confirm = dialog.getByRole('button', { name: 'Turn on for #team' })
  await expect(confirm).toBeDisabled() // not until "I understand"
  await shot(page, 'ambient-confirm', info.project.name)
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await page.reload()
  await expect(page.getByRole('group', { name: 'Reading whole channels' }).getByRole('switch', { name: '#team' })).toHaveAttribute('aria-checked', 'false')

  const again = page.getByRole('group', { name: 'Reading whole channels' })
  await again.getByRole('switch', { name: '#team' }).click()
  await page.getByRole('checkbox', { name: 'I understand' }).check()
  await page.getByRole('button', { name: 'Turn on for #team' }).click()
  await expect(again.getByRole('switch', { name: '#team' })).toHaveAttribute('aria-checked', 'true')
  await expect(again.getByText('Ava now reads #team.')).toBeVisible()
  await expect(again.getByLabel('Keep messages from #team for')).toHaveValue('30')
  await again.getByLabel('Keep messages from #team for').selectOption('7')
  await expect(again.getByText('Retention saved.')).toBeVisible()
  await page.reload()
  const after = page.getByRole('group', { name: 'Reading whole channels' })
  await expect(after.getByRole('switch', { name: '#team' })).toHaveAttribute('aria-checked', 'true')
  await expect(after.getByLabel('Keep messages from #team for')).toHaveValue('7')
  const axe = await new AxeBuilder({ page }).include('#ambient').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  await after.scrollIntoViewIfNeeded()
  await shot(page, 'ambient-on', info.project.name)
})

test('stopping can delete what was stored, and a stop without deleting keeps it', async ({ page, owner }) => {
  void owner
  await plantSlackChannel('C0TEAM01', 'team', 3)
  await page.goto('/dashboard/connections')
  const group = page.getByRole('group', { name: 'Reading whole channels' })
  await group.getByRole('switch', { name: '#team' }).click()
  await page.getByRole('checkbox', { name: 'I understand' }).check()
  await page.getByRole('button', { name: 'Turn on for #team' }).click()
  await expect(group.getByText('3 messages stored')).toBeVisible()

  await group.getByRole('switch', { name: '#team' }).click()
  await page.getByRole('button', { name: 'Stop and keep what is stored' }).click()
  await expect(group.getByText('Off. 3 earlier messages still stored')).toBeVisible()
  expect(await storedRoomMessages('C0TEAM01')).toBe(3)

  await group.getByRole('button', { name: 'Delete the 3 stored messages' }).click()
  await expect(group.getByText('Deleted what was stored from #team.')).toBeVisible()
  expect(await storedRoomMessages('C0TEAM01')).toBe(0)
})

test('a Slack app missing the listing permissions is named', async ({ page, owner }) => {
  void owner
  await plantMissingScopes('slack', ['channels:read', 'groups:read'])
  await page.goto('/dashboard/connections')
  await expect(page.getByTestId('ambient-missing-scopes')).toContainText('channels:read, groups:read')
  await expect(page.getByText('Connect Slack first')).toBeVisible()
})

test('at 390px the switch, the confirmation and the box fit the screen', async ({ page, owner }, info) => {
  void owner
  await plantSlackChannel('C0TEAM01', 'team', 2)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/dashboard/connections')
  const group = page.getByRole('group', { name: 'Reading whole channels' })
  await group.scrollIntoViewIfNeeded()
  expect(await noSideScroll(page)).toBe(true)
  await group.getByRole('switch', { name: '#team' }).click()
  const dialog = page.getByRole('dialog', { name: /Let Ava read all of #team\?/ })
  await expect(dialog).toBeVisible()
  const box = await dialog.boundingBox()
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBe(true)
  await shot(page, 'ambient-confirm-390', info.project.name)
  await page.getByRole('checkbox', { name: 'I understand' }).check()
  await page.getByRole('button', { name: 'Turn on for #team' }).click()
  await expect(group.getByRole('switch', { name: '#team' })).toHaveAttribute('aria-checked', 'true')
  expect(await noSideScroll(page)).toBe(true)
  await group.scrollIntoViewIfNeeded()
  await shot(page, 'ambient-on-390', info.project.name)
})
