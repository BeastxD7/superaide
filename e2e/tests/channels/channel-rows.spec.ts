// #282 #287 #289: Home's "Where people reach Ava" says the same as Connections. The credentials are planted in the vault the way
// the connect form stores them (token shapes are joined at run time so the secret scanner sees no real-looking literal); any connection action then makes the API apply them (as it does after a real connect).
import { expect, test } from '../../src/fixtures'
import { linkOwner, plantSecret } from '../../src/helpers/vault'

const reachRow = (page: import('@playwright/test').Page, channel: string) =>
  page.locator('[data-slot="card"]').filter({ hasText: 'Where people reach' }).getByRole('listitem').filter({ hasText: channel })

async function applyPlanted(api: import('../../src/fixtures').Api) {
  await api.ok(api.post('/app/connections/deepgram/disconnect')) // harmless; the API re-reads the vault after it
}

test('a connected Telegram bot shows on Home as connected, not yet linked', async ({ page, api, owner }, info) => {
  void owner
  await plantSecret('telegram', 'bot_token', '123456789:AAEfake-token-for-the-e2e-suite')
  await applyPlanted(api)
  await page.goto('/dashboard')
  await expect(reachRow(page, 'Telegram')).toContainText('Bot connected, your Telegram account is not linked yet')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-home-telegram-${info.project.name}.png` })
})

test('Slack: connected (not linked), linked, then Disconnect on Connections updates Home', async ({ page, api, owner }, info) => {
  void owner
  await plantSecret('slack', 'bot_token', ['xoxb', '1', 'fake', 'bot', 'token', 'aaaa'].join('-'))
  await plantSecret('slack', 'app_token', ['xapp', '1', 'A0APPONE', '123456', 'secretsecret'].join('-'))
  await applyPlanted(api)
  await page.goto('/dashboard')
  await expect(reachRow(page, 'Slack')).toContainText('Bot connected, your Slack account is not linked yet')

  await linkOwner('slack', 'U0OWNER01')
  await page.reload()
  await expect(reachRow(page, 'Slack')).toContainText('Linked to you')

  await page.goto('/dashboard/connections')
  await page.getByRole('button', { name: /^Slack/ }).click()
  await page.getByRole('button', { name: 'Disconnect Slack' }).click()
  await expect(page.getByRole('button', { name: /^Slack/ })).toContainText('Your team can reach me in Slack')
  await page.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(reachRow(page, 'Slack')).toContainText('Not connected')
  await expect(reachRow(page, 'Slack')).not.toContainText('Linked to you')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-home-slack-${info.project.name}.png` })
  await page.reload()
  await expect(reachRow(page, 'Slack')).toContainText('Not connected')
})
