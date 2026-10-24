// #441: Gmail's reading rules and drafts use the shared setting row: title, why, example, status, control and
// "last changed by/when" from the audit log.
import AxeBuilder from '@axe-core/playwright'
import { expect, query, test } from '../../src/fixtures'
import { plantSecret } from '../../src/helpers/vault'

const CLIENT = 'abc.apps.googleusercontent.com'

async function connectGmail(api: import('../../src/fixtures').Api) {
  await plantSecret('google', 'client_id', CLIENT)
  await plantSecret('google', 'client_secret', 'GOCSPX-secretsecret')
  await plantSecret('google', 'gmail_refresh_token', 'rt-test-only-placeholder')
  await query(`UPDATE integration_secret SET meta = $1::jsonb WHERE name = 'gmail_refresh_token'`, [
    JSON.stringify({ scopes: ['https://www.googleapis.com/auth/gmail.readonly'], client_id: CLIENT, address: 'ananya@example.com' }),
  ])
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const gmail = cards.find((c) => c.id === 'superaide/gmail')!
  await api.ok(api.post('/app/skills/superaide/gmail/install', { accepted: gmail.permissions.map((p) => p.id) }))
}

test('every reading rule is a row with a status badge and "Never changed", then names who changed it and when', async ({ page, api, owner }, info) => {
  void owner
  await connectGmail(api)
  await page.goto('/dashboard/capabilities/gmail/reading')
  const rows = page.getByTestId('setting-row')
  await expect(rows).toHaveCount(12) // ten reading rules plus the two inbox sorter rows (#469)
  await expect(rows.first()).toContainText('Never changed')
  await expect(rows.first()).toContainText('Default')
  const numbers = rows.filter({ hasText: 'Numbers in emails' })
  await numbers.getByRole('combobox').selectOption({ index: 1 })
  await expect(page.getByText('Numbers in emails saved.')).toBeVisible()
  await expect(numbers).toContainText('Changed')
  await expect(numbers).toContainText(/Last changed by you, Today, /)
  await expect(rows.filter({ hasText: 'Search size' })).toContainText('Never changed') // only the changed one has an entry
  await page.reload()
  await expect(rows.filter({ hasText: 'Numbers in emails' })).toContainText(/Last changed by you/)
  await page.waitForTimeout(500)
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-rows-reading-${info.project.name}.png`, fullPage: true })
})

test('the Drafts page has its own switch row, with the audit line, and no axe violations', async ({ page, api, owner }, info) => {
  void owner
  await connectGmail(api)
  await page.goto('/dashboard/capabilities/gmail/drafts')
  const row = page.getByTestId('setting-row')
  await expect(row).toContainText('Off (default)')
  await expect(row).toContainText('Never changed')
  await expect(row.getByRole('switch', { name: 'Save reply drafts in my Gmail' })).not.toBeChecked()
  await api.ok(api.post('/app/gmail/drafts', { enabled: false })) // an owner change writes an audit line
  await page.reload()
  await expect(page.getByTestId('setting-row')).toContainText(/Last changed by you, Today, /)
  await page.waitForTimeout(500)
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-rows-drafts-${info.project.name}.png`, fullPage: true })
})
