// #666: the Tools (MCP) page. Add a server, test the connection, see the tools, set a risk class, approve, switch off, remove.
import path from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

const FAKE = path.resolve(process.cwd(), 'fixtures/fake-mcp.mjs')

test('add a program server, see its tools, set a risk class, approve, switch off and remove', async ({ page, owner }, info) => {
  void owner
  await page.goto('/dashboard/tools')
  await expect(page.getByRole('heading', { name: 'Tools (MCP)' })).toBeVisible()
  await expect(page.getByText('No servers yet.')).toBeVisible()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/666-empty-${info.project.name}.png` })

  await page.getByRole('button', { name: 'Add a server' }).click()
  const form = page.getByRole('form', { name: 'Add a server' })
  await form.getByLabel('Name').fill('Team notes')
  await form.getByLabel('Program on this computer').check()
  await form.getByLabel('Command').fill(process.execPath)
  await form.getByLabel('Arguments, one per line').fill(FAKE)
  await form.getByRole('button', { name: 'Add server' }).click()

  const dialog = page.getByRole('dialog', { name: 'Run a program on this computer?' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await dialog.getByLabel('I understand').check()
  await dialog.getByRole('button', { name: 'Add the server' }).click()

  const card = page.getByRole('region', { name: 'Team notes' })
  await expect(card).toBeVisible()
  await expect(card.getByText('Press Test connection to see its tools.')).toBeVisible()
  await card.getByRole('button', { name: 'Test connection' }).click()
  const tools = card.getByRole('list', { name: 'Tools of Team notes' })
  await expect(tools.getByText('search_notes')).toBeVisible()
  await expect(tools.getByText('create_note')).toBeVisible()
  await expect(tools.getByText('delete_note')).toBeVisible()
  await expect(tools.getByText('Not approved')).toHaveCount(3)
  // Unknown means write, except what the server marks read-only or destructive.
  await expect(card.getByLabel('Risk class for create_note')).toHaveValue('write')
  await expect(card.getByLabel('Risk class for delete_note')).toHaveValue('destructive')

  await card.getByLabel('Risk class for create_note').selectOption('read')
  await expect(card.getByLabel('Risk class for create_note')).toHaveValue('read')
  await page.reload()
  await expect(page.getByRole('region', { name: 'Team notes' }).getByLabel('Risk class for create_note')).toHaveValue('read')

  await card.getByRole('button', { name: 'Approve 3 tools as listed' }).click()
  await expect(card.getByRole('button', { name: 'All tools approved' })).toBeDisabled()
  await expect(tools.getByText('Approved', { exact: true })).toHaveCount(3)
  const axe = await new AxeBuilder({ page }).include('main').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/666-tools-${info.project.name}.png`, fullPage: true })

  await card.getByRole('switch', { name: 'Team notes is on' }).click()
  await expect(card.getByText('Off', { exact: true })).toBeVisible()
  page.once('dialog', (d) => void d.accept())
  await card.getByRole('button', { name: 'Remove' }).click()
  await expect(page.getByText('No servers yet.')).toBeVisible()
})

test('a program that is not there is reported, and cancelling the warning adds nothing', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/tools')
  await page.getByRole('button', { name: 'Add a server' }).click()
  const form = page.getByRole('form', { name: 'Add a server' })
  await form.getByLabel('Name').fill('Broken one')
  await form.getByLabel('Program on this computer').check()
  await form.getByLabel('Command').fill('/no/such/program')
  await form.getByRole('button', { name: 'Add server' }).click()
  const dialog = page.getByRole('dialog', { name: 'Run a program on this computer?' })
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByText('No servers yet.')).toBeVisible()
  await form.getByRole('button', { name: 'Add server' }).click()
  await dialog.getByLabel('I understand').check()
  await dialog.getByRole('button', { name: 'Add the server' }).click()
  const card = page.getByRole('region', { name: 'Broken one' })
  await card.getByRole('button', { name: 'Test connection' }).click()
  await expect(card.getByText('The command was not found on this computer.')).toBeVisible()
})

test('a visitor with no account gets nothing', async ({ page }) => {
  await page.goto('/dashboard/tools')
  await expect(page).not.toHaveURL(/dashboard\/tools$/)
})
