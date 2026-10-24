// #441: the Google OAuth client and sign-in (used by Gmail and Calendar) are a card on Capabilities, not a row on Connections.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

test('the Google account card is on Capabilities; Connections only points to it', async ({ page, owner }, info) => {
  void owner
  await page.goto('/dashboard/connections')
  await expect(page.getByRole('button', { name: /^Google Calendar/ })).toHaveCount(0)
  const pointer = page.getByRole('link', { name: /Google \(Gmail and Calendar\)/ })
  await expect(pointer).toBeVisible()
  await pointer.click()
  await expect(page).toHaveURL(/\/dashboard\/capabilities#google$/)
  const card = page.getByRole('region', { name: 'Google account' })
  await expect(card).toBeVisible()
  await expect(card.getByRole('button', { name: /^Google Calendar/ })).toContainText('Connect')
  await page.waitForTimeout(500)
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-google-card-${info.project.name}.png`, fullPage: true })
})

test.describe('what Google sends back', () => {
  const connected = async (page: import('@playwright/test').Page) =>
    page.route(
      (url) => url.pathname === '/api/app/connections',
      async (route) => {
        const response = await route.fetch()
        const body = await response.json()
        body.services.google = { connected: true, via: 'app', client_ready: true, hints: { client_id: 'abc…com' }, meta: { scopes: [] }, since: null }
        await route.fulfill({ response, json: body })
      },
    )

  test('"actions-enabled" says Ava can change the calendar; "connected" and an old or unknown value say only that it is connected', async ({ page, owner }) => {
    void owner
    await connected(page)
    await page.goto('/dashboard/capabilities?google=actions-enabled')
    await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('I can now add, move and decline events')
    await expect(page).toHaveURL(/\/dashboard\/capabilities$/)
    await page.goto('/dashboard/capabilities?google=connected')
    await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('Google Calendar is connected')
    await page.goto('/dashboard/capabilities?google=something-from-an-older-release')
    await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('Google Calendar is connected')
  })

  test('a crafted "actions-enabled" link does not announce a connection that does not exist', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/capabilities?google=actions-enabled')
    await expect(page.getByRole('heading', { level: 1, name: 'Capabilities' })).toBeVisible()
    await expect(page.getByText('I can now add, move and decline events')).toHaveCount(0)
    await expect(page).toHaveURL(/\/dashboard\/capabilities$/)
  })
})
