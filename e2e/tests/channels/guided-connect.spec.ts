// #410: guided connect for Slack and Telegram on Connections: a manifest for the chosen app name, each pasted token
// checked with its service before saving (the service answers are planted with page.route: no real Slack or Telegram),
// every error naming the exact scope or setting, and a test message once the owner's account is linked.
import type { Page, Route } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { linkOwner, plantSecret } from '../../src/helpers/vault'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

const BOT = ['xoxb', '1111', '2222', 'abcdefghijklmnop'].join('-')
const APP = ['xapp', '1', 'A111', '2222', 'abcdefghijklmnop'].join('-')
const TG = '123456789:' + 'A'.repeat(35)

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function open(page: Page, row: RegExp) {
  await page.goto('/dashboard/connections')
  await page.getByRole('button', { name: row }).click()
}
const shot = async (page: Page, name: string, info: { project: { name: string } }) => {
  await page.waitForTimeout(500) // the row's open animation
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}-${info.project.name}.png` })
}

test.describe('Slack', () => {
  test('the app name goes into the manifest and the create link, and the manifest downloads', async ({ page, owner }, info) => {
    void owner
    await open(page, /^Slack/)
    const name = page.getByLabel('Name of your Slack app')
    await expect(name).toHaveValue('Ava')
    await name.fill("Jordan's helper")
    const create = page.getByRole('link', { name: 'Create my Slack app' })
    await expect(create).toHaveAttribute('href', /manifest_yaml=.*Jordan/)
    const href = (await create.getAttribute('href')) ?? ''
    const manifest = new URL(href).searchParams.get('manifest_yaml') ?? ''
    expect(manifest).toContain("name: Jordan's helper")
    expect(manifest).toContain('socket_mode_enabled: true')
    expect(manifest).toContain('chat:write')
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download the manifest' }).click()])
    expect(download.suggestedFilename()).toBe('slack-app-manifest.yml')
    await shot(page, 'slack-name', info)
  })

  test('a name with markup or line breaks cannot change the permissions asked for', async ({ page, owner }) => {
    void owner
    await open(page, /^Slack/)
    await page.getByLabel('Name of your Slack app').fill('Meera\nscopes: [admin]<b>')
    const create = page.getByRole('link', { name: 'Create my Slack app' })
    await expect(create).toHaveAttribute('href', /Meera%20scopes%20admin/)
    const manifest = new URL((await create.getAttribute('href')) ?? '').searchParams.get('manifest_yaml') ?? ''
    expect(manifest).not.toContain('admin]')
    expect(manifest).not.toContain('<b>')
  })

  test('a bot token of the wrong kind is refused as it is pasted, naming what it should start with', async ({ page, owner }) => {
    void owner
    await open(page, /^Slack/)
    await page.getByLabel('Bot User OAuth Token').fill('xoxp-this-is-a-user-token')
    await expect(page.getByTestId('verdict-check-slack-bot_token')).toContainText('should start with xoxb-')
    await page.getByLabel('App-level token').fill('xoxb-this-is-not-an-app-token')
    await expect(page.getByTestId('verdict-check-slack-app_token')).toContainText('should start with xapp-')
    await expect(page.locator('body')).not.toContainText('xoxp-this-is-a-user-token')
  })

  test('each token shows Slack\'s verdict, the missing scope by name, and optional ones as optional', async ({ page, owner }, info) => {
    void owner
    const answers: Record<string, unknown> = {
      [BOT]: { bot: { ok: true, missing_required: [], missing_optional: ['channels:history', 'files:read'], scopes_known: true }, app: null },
      [APP]: { bot: null, app: { ok: false, error: "Slack didn't accept the app-level token. It needs the connections:write scope: under Basic Information > App-Level Tokens, make a new one with it." } },
    }
    await page.route('**/api/app/connections/slack/check', (route) => {
      const body = route.request().postDataJSON() as { bot_token?: string; app_token?: string }
      return json(route, answers[body.bot_token ?? body.app_token ?? ''])
    })
    await open(page, /^Slack/)
    await page.getByLabel('Bot User OAuth Token').fill(BOT)
    await expect(page.getByTestId('verdict-check-slack-bot_token')).toContainText('Slack accepts this token')
    await expect(page.getByTestId('optional-missing')).toContainText('channels:history, files:read')
    await page.getByLabel('App-level token').fill(APP)
    await expect(page.getByTestId('verdict-check-slack-app_token')).toContainText('connections:write')
    await expect(page.getByTestId('verdict-check-slack-app_token').getByRole('alert')).toBeVisible()
    await shot(page, 'slack-verdicts', info)
  })

  test('a bot token missing a needed permission names it', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/connections/slack/check', (route) =>
      json(route, { bot: { ok: false, error: 'The Slack app is missing permissions: chat:write.', missing_required: ['chat:write'], missing_optional: [] }, app: null }),
    )
    await open(page, /^Slack/)
    await page.getByLabel('Bot User OAuth Token').fill(BOT)
    await expect(page.getByTestId('missing-required')).toHaveText('Missing: chat:write')
  })

  test('a check that cannot run does not block saving', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/connections/slack/check', (route) => json(route, { detail: 'boom' }, 500))
    await open(page, /^Slack/)
    await page.getByLabel('Bot User OAuth Token').fill(BOT)
    await page.getByLabel('App-level token').fill(APP)
    await expect(page.getByTestId('verdict-check-slack-bot_token')).toContainText("Couldn't check this right now")
    await expect(page.getByRole('button', { name: 'Check and save' })).toBeEnabled()
  })
})

test.describe('Telegram', () => {
  test('a token that is not a bot token is refused as it is pasted', async ({ page, owner }) => {
    void owner
    await open(page, /^Telegram/)
    await page.getByLabel('Bot token from BotFather').fill('definitely not a bot token')
    await expect(page.getByTestId('verdict-check-telegram-bot_token')).toContainText("doesn't look like a bot token")
  })

  test('a token Telegram accepts names the bot', async ({ page, owner }, info) => {
    void owner
    await page.route('**/api/app/connections/telegram/check', (route) => json(route, { ok: true, username: 'ava_for_jordan_bot', name: 'Ava' }))
    await open(page, /^Telegram/)
    await page.getByLabel('Bot token from BotFather').fill(TG)
    await expect(page.getByTestId('verdict-check-telegram-bot_token')).toContainText('@ava_for_jordan_bot')
    await expect(page.locator('body')).not.toContainText(TG)
    await shot(page, 'telegram-ok', info)
  })

  test('a token Telegram refuses says to copy it again', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/connections/telegram/check', (route) => json(route, { ok: false, error: "Telegram didn't accept that token. Copy it again from @BotFather." }))
    await open(page, /^Telegram/)
    await page.getByLabel('Bot token from BotFather').fill(TG)
    await expect(page.getByTestId('verdict-check-telegram-bot_token').getByRole('alert')).toContainText('Copy it again')
  })
})

test.describe('test message', () => {
  test('after the account is linked, one button sends a test message and says so', async ({ page, owner }, info) => {
    void owner
    await plantSecret('telegram', 'bot_token', TG)
    await linkOwner('telegram', '555000111')
    let sent = 0
    await page.route('**/api/app/connections/telegram/test-message', (route) => {
      sent++
      return json(route, { sent: true })
    })
    await open(page, /^Telegram/)
    await page.getByRole('button', { name: 'Send a test message on Telegram' }).click()
    await expect(page.getByText('Sent. Check Telegram.')).toBeVisible()
    expect(sent).toBe(1)
    await shot(page, 'test-message', info)
  })

  test('a refused test message says what to do, not "failed"', async ({ page, owner }) => {
    void owner
    await plantSecret('telegram', 'bot_token', TG)
    await linkOwner('telegram', '555000111')
    await page.route('**/api/app/connections/telegram/test-message', (route) => json(route, { detail: "Telegram didn't accept the message." }, 502))
    await open(page, /^Telegram/)
    await page.getByRole('button', { name: 'Send a test message on Telegram' }).click()
    await expect(page.getByText("Telegram didn't accept the message.")).toBeVisible()
  })

  test('there is no test button before the owner links their account', async ({ page, owner }) => {
    void owner
    await plantSecret('telegram', 'bot_token', TG)
    await open(page, /^Telegram/)
    await expect(page.getByTestId('test-message')).toHaveCount(0)
  })
})
