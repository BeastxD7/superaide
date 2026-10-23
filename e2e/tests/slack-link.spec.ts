// Linking the owner's own Slack account: Connections shows a one-time code to send in a direct message to Ava, and
// Home tells "app connected, you not linked" apart from "not connected". The Slack service state is rewritten in the
// browser (a real Slack app would call slack.com); the code is made by the real API for the refusal case.
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { goToDashboardPage, homeHeading, openConnections } from '../src/helpers/workspace'

const SHOTS = process.env.SHOTS_DIR

async function slackConnected(page: Page, opts: { linked: () => boolean }) {
  await page.route('**/api/app/connections', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.services.slack = {
      connected: true,
      via: 'app',
      hints: { bot_token: 'xoxb…abcd' },
      meta: { team: 'Acme' },
      health: { status: 'ok' },
    }
    await route.fulfill({ response: res, json: body })
  })
  await page.route('**/api/app/me', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.channels.slack_available = true
    body.channels.slack = opts.linked()
    await route.fulfill({ response: res, json: body })
  })
}

test.describe('Slack: link my account', () => {
  test('the card makes a code, says what to send, and flips to linked once Ava has it', async ({ page, owner }) => {
    void owner
    let linked = false
    await slackConnected(page, { linked: () => linked })
    const expiresAt = new Date(Date.now() + 15 * 60_000).toISOString()
    await page.route('**/api/app/channels/slack/invite', (route) =>
      route.fulfill({
        json: {
          code: 'Ab3dEf9hIjKlMnOp',
          expires_minutes: 15,
          expires_at: expiresAt,
          instruction: 'Open a direct message with Ava in Slack and send: link Ab3dEf9hIjKlMnOp',
        },
      }),
    )
    await openConnections(page)
    await page.getByRole('button', { name: /^Slack/ }).click()
    await expect(page.getByText(/Slack app connected\. Now link your Slack account/)).toBeVisible()
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/connections-slack-before-code.png` })
    await page.getByRole('button', { name: 'Link my Slack account' }).click()
    await expect(page.getByText('Open a direct message with Ava in Slack and send:')).toBeVisible()
    await expect(page.getByText('link Ab3dEf9hIjKlMnOp', { exact: true })).toBeVisible()
    await expect(page.getByText(/Works once, for 1[45]:\d\d more/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Copy the message' })).toBeVisible()
    if (SHOTS) {
      await page.waitForTimeout(500) // the card's height animation, for the picture only
      await page.screenshot({ path: `${SHOTS}/connections-slack-code.png` })
    }
    linked = true // Ava received the code in a DM; the card notices on its next check
    await expect(page.getByText('Your Slack account is linked.')).toBeVisible({ timeout: 10_000 })
    if (SHOTS) {
      await page.waitForTimeout(500)
      await page.screenshot({ path: `${SHOTS}/connections-slack-linked.png` })
    }
  })

  test('a failing invite says so and offers the button again', async ({ page, owner }) => {
    void owner
    await slackConnected(page, { linked: () => false })
    await page.route('**/api/app/channels/slack/invite', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Something went wrong on our side.' } }),
    )
    await openConnections(page)
    await page.getByRole('button', { name: /^Slack/ }).click()
    await page.getByRole('button', { name: 'Link my Slack account' }).click()
    await expect(page.getByText('Something went wrong on our side.')).toBeVisible({ timeout: 5000 })
    await expect(page.getByRole('button', { name: 'Link my Slack account' })).toBeVisible()
  })

  test('home says "not connected", then "app connected, you not linked", then "linked"', async ({ page, owner }) => {
    await page.goto('/dashboard')
    await expect(homeHeading(page, owner)).toBeVisible()
    const reach = page.locator('[data-slot="card"]').filter({ hasText: 'Where people reach Ava' })
    const slack = reach.getByRole('listitem').filter({ hasText: 'Slack' })
    await expect(slack).toContainText('Not connected')
    if (SHOTS) await reach.screenshot({ path: `${SHOTS}/home-reach-not-connected.png` })

    let linked = false
    await slackConnected(page, { linked: () => linked })
    await openConnections(page)
    await goToDashboardPage(page, 'Home')
    await expect(slack).toContainText('Bot connected, your Slack account is not linked yet')
    await expect(slack).not.toContainText('Not connected')
    await expect(slack.getByRole('link', { name: /not linked yet/ })).toHaveAttribute('href', '/dashboard/connections')
    if (SHOTS) await reach.screenshot({ path: `${SHOTS}/home-reach-slack-unlinked.png` })

    linked = true
    await page.reload()
    await expect(slack).toContainText('Linked to you')
  })

  test('the invite is refused with no Slack app connected, and without the CSRF header', async ({ page, owner }) => {
    void owner
    const none = await page.request.post('/api/app/channels/slack/invite', { headers: { 'X-Requested-With': 'superaide' } })
    expect(none.status()).toBe(404)
    const noCsrf = await page.request.post('/api/app/channels/slack/invite')
    expect([400, 403]).toContain(noCsrf.status())
  })
})
