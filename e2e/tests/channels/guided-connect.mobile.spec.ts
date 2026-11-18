// #410 on a phone: the Slack and Telegram guides, the per-token verdicts and the test message fit the screen.
import type { Page, Route } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { linkOwner, plantSecret } from '../../src/helpers/vault'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

const BOT = ['xoxb', '1111', '2222', 'abcdefghijklmnop'].join('-')
const TG = '123456789:' + 'A'.repeat(35)
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function open(page: Page, row: RegExp) {
  await page.goto('/dashboard/connections')
  await page.getByRole('button', { name: row }).click()
}
const shot = async (page: Page, name: string) => {
  await page.waitForTimeout(500) // the row's open animation
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}-mobile.png` })
}

test('the Slack guide: name, create link, manifest download and a missing-scope verdict fit the phone', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/connections/slack/check', (route) =>
    json(route, { bot: { ok: false, error: 'The Slack app is missing permissions: chat:write, im:write.', missing_required: ['chat:write', 'im:write'], missing_optional: [] }, app: null }),
  )
  await open(page, /^Slack/)
  await page.getByLabel('Name of your Slack app').fill('Ava for Jordan')
  await expect(page.getByRole('link', { name: 'Create my Slack app' })).toHaveAttribute('href', /manifest_yaml=.*Jordan/)
  await expect(page.getByRole('button', { name: 'Download the manifest' })).toBeVisible()
  await page.getByLabel('Bot User OAuth Token').fill(BOT)
  await expect(page.getByTestId('missing-required')).toContainText('chat:write, im:write')
  await expectNoHorizontalScroll(page)
  await shot(page, 'slack-guide')
})

test('the Telegram verdict and the test message fit the phone', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/connections/telegram/check', (route) => json(route, { ok: false, error: "Telegram didn't accept that token. Copy it again from @BotFather." }))
  await open(page, /^Telegram/)
  await page.getByLabel('Bot token from BotFather').fill(TG)
  await expect(page.getByTestId('verdict-check-telegram-bot_token')).toContainText('Copy it again')
  await expectNoHorizontalScroll(page)
  await shot(page, 'telegram-verdict')
})

test('a linked Telegram account gets the test message button', async ({ page, owner }) => {
  void owner
  await plantSecret('telegram', 'bot_token', TG)
  await linkOwner('telegram', '555000111')
  await page.route('**/api/app/connections/telegram/test-message', (route) => json(route, { sent: true }))
  await open(page, /^Telegram/)
  await page.getByRole('button', { name: 'Send a test message on Telegram' }).click()
  await expect(page.getByText('Sent. Check Telegram.')).toBeVisible()
  await expectNoHorizontalScroll(page)
  await shot(page, 'test-message')
})
