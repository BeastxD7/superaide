// #447: a Slack app that lacks permissions is named on Connections and in a banner; a complete one shows nothing.
// The startup/connect check records its verdict in the database; here the verdict is planted (no call to Slack).
import { expect, test } from '../../src/fixtures'
import { plantMissingScopes, plantSecret } from '../../src/helpers/vault'

async function connectSlack(api: import('../../src/fixtures').Api) {
  await plantSecret('slack', 'bot_token', ['xoxb', '1', 'fake', 'bot', 'token', 'aaaa'].join('-'))
  await plantSecret('slack', 'app_token', ['xapp', '1', 'A0APPONE', '123456', 'secretsecret'].join('-'))
  await api.ok(api.post('/app/connections/deepgram/disconnect')) // harmless; the API re-reads the vault after it
}

test('missing Slack scopes are named on Connections and in the banner', async ({ page, api, owner }, info) => {
  void owner
  await connectSlack(api)
  await plantMissingScopes('slack', ['channels:history', 'groups:history'])
  await page.goto('/dashboard/connections')
  const banner = page.getByTestId('slack-scopes-banner')
  await expect(banner).toContainText('channels:history, groups:history')
  await page.getByRole('button', { name: /^Slack/ }).click()
  await expect(page.getByTestId('slack-missing-scopes')).toContainText('reinstall')
  await expect(page.getByRole('button', { name: /^Slack/ })).toContainText('Missing Slack permissions')
  await page.waitForTimeout(600) // the row's open animation
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-slack-scopes-${info.project.name}.png` })
})

test('a Slack app with every permission shows no notice', async ({ page, api, owner }, info) => {
  void owner
  await connectSlack(api)
  await plantMissingScopes('slack', [])
  await page.goto('/dashboard/connections')
  await expect(page.getByRole('button', { name: /^Slack/ })).toContainText('Connected')
  await expect(page.getByTestId('slack-scopes-banner')).toHaveCount(0)
  await page.getByRole('button', { name: /^Slack/ }).click()
  await expect(page.getByTestId('slack-missing-scopes')).toHaveCount(0)
  await page.waitForTimeout(600) // the row's open animation
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-slack-all-scopes-${info.project.name}.png` })
})

test('at 390px the notice fits the screen', async ({ page, api, owner }, info) => {
  void owner
  await connectSlack(api)
  await plantMissingScopes('slack', ['channels:history', 'groups:history'])
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/dashboard/connections')
  await page.getByRole('button', { name: /^Slack/ }).click()
  await expect(page.getByTestId('slack-missing-scopes')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.waitForTimeout(600) // the row's open animation
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-slack-scopes-390-${info.project.name}.png` })
})
