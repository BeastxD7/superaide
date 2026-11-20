// #666 at 390px: the Tools (MCP) page fits a phone and nothing scrolls sideways.
import AxeBuilder from '@axe-core/playwright'
import path from 'node:path'
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })
const FAKE = path.resolve(process.cwd(), 'fixtures/fake-mcp.mjs')

test('the Tools page fits 390px', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/tools')
  await expect(page.getByRole('heading', { name: 'Tools (MCP)' })).toBeVisible()
  await expectNoHorizontalScroll(page)
  await page.getByRole('button', { name: 'Add a server' }).click()
  const form = page.getByRole('form', { name: 'Add a server' })
  await form.getByLabel('Name').fill('Team notes')
  await form.getByLabel('Program on this computer').check()
  await form.getByLabel('Command').fill(process.execPath)
  await form.getByLabel('Arguments, one per line').fill(FAKE)
  await form.getByRole('button', { name: 'Add server' }).click()
  const dialog = page.getByRole('dialog', { name: 'Run a program on this computer?' })
  await expectNoHorizontalScroll(page)
  await dialog.getByLabel('I understand').check()
  await dialog.getByRole('button', { name: 'Add the server' }).click()
  const card = page.getByRole('region', { name: 'Team notes' })
  await card.getByRole('button', { name: 'Test connection' }).click()
  await expect(card.getByText('delete_note')).toBeVisible()
  await expectNoHorizontalScroll(page)
  const axe = await new AxeBuilder({ page }).include('main').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/666-390.png`, fullPage: true })
})
