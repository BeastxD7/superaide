// Google Calendar on the Connections page: save your own OAuth client, sign in with
// Google, come back to a clear result. Google is never contacted: the browser's trip to
// accounts.google.com is answered here, and the outcomes that would need the backend to
// trade a code with Google (connected) are shown with a mocked status instead.
import type { Page, Route } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { apiEnv } from '../../src/env'
import { openConnections, storedSecrets } from '../../src/helpers/workspace'

const CLIENT_ID = '123456789012-e2etestclient.apps.googleusercontent.com'
const CLIENT_SECRET = 'e2e-placeholder-client-secret-1234'
const REDIRECT_URI = `${apiEnv().FRONTEND_ORIGIN}/api/app/connections/google/callback`

const googleRow = (page: Page) => page.getByRole('button', { name: /^Google Calendar/ })

// Anything sent to Google that a test didn't answer is stopped and recorded.
async function blockGoogle(page: Page): Promise<string[]> {
  const attempted: string[] = []
  await page.route(
    (url) => url.hostname.endsWith('google.com') || url.hostname.endsWith('googleapis.com'),
    async (route) => {
      attempted.push(route.request().url())
      await route.abort('blockedbyclient')
    },
  )
  return attempted
}

// Pretend to be Google's consent screen: note the sign-in URL, then send the browser
// back to the app's callback the way Google would.
async function fakeGoogleConsent(page: Page, answer: (state: string) => Record<string, string>): Promise<{ url: URL | null }> {
  const seen: { url: URL | null } = { url: null }
  await page.route('https://accounts.google.com/**', async (route: Route) => {
    const url = new URL(route.request().url())
    seen.url = url
    const back = new URL(REDIRECT_URI)
    for (const [k, v] of Object.entries(answer(url.searchParams.get('state') ?? ''))) back.searchParams.set(k, v)
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<!doctype html><title>Google</title><script>location.replace(${JSON.stringify(back.toString())})</script>`,
    })
  })
  return seen
}

async function saveClient(page: Page) {
  await page.getByLabel('Client ID').fill(CLIENT_ID)
  await page.getByLabel('Client secret').fill(CLIENT_SECRET)
  await page.getByRole('button', { name: 'Save OAuth client' }).click()
  await expect(googleRow(page)).toContainText('OAuth client saved')
}

test('set up the OAuth client, go to Google, and come back cancelled', async ({ page, owner, context }) => {
  void owner
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const outside = await blockGoogle(page)
  await openConnections(page)
  const row = googleRow(page)
  await expect(row).toHaveAttribute('aria-expanded', 'false')
  await row.click()
  await expect(row).toHaveAttribute('aria-expanded', 'true')

  // Step 1: the help, the exact redirect URI with a copy button, and the read-only scope.
  await expect(page.getByText('create an OAuth client ID of type Web application', { exact: false })).toBeVisible()
  await expect(page.getByLabel('Redirect URI')).toHaveText(REDIRECT_URI)
  await page.getByRole('button', { name: 'Copy' }).click()
  await expect(page.getByRole('button', { name: 'Copied' })).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(REDIRECT_URI)
  await expect(page.getByText('calendar.readonly', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open Google Cloud credentials' })).toHaveAttribute('href', /console\.cloud\.google\.com/)
  await expect(page.getByLabel('Client secret')).toHaveAttribute('type', 'password')
  const connect = page.getByRole('button', { name: 'Connect Google Calendar', exact: true })
  await expect(connect, 'no sign-in before a client is saved').toBeDisabled()

  // A wrong client ID is refused with a clear message, and nothing is stored.
  await page.getByLabel('Client ID').fill('not-a-google-client-id')
  await page.getByLabel('Client secret').fill(CLIENT_SECRET)
  await page.getByRole('button', { name: 'Save OAuth client' }).click()
  await expect(page.getByText("That doesn't look like a Google client ID")).toBeVisible()
  expect(await storedSecrets()).toEqual([])

  // The right one is saved encrypted; the secret is only ever shown as a hint.
  await page.getByLabel('Client ID').fill('')
  await saveClient(page)
  const stored = await storedSecrets()
  expect(stored.map((s) => s.name)).toEqual(['client_id', 'client_secret'])
  expect(stored.find((s) => s.name === 'client_secret')?.hint).not.toContain(CLIENT_SECRET)
  expect(await page.locator('body').innerText()).not.toContain(CLIENT_SECRET)

  // Step 2: off to Google (PKCE, read-only, our redirect URI), where the owner says no.
  const google = await fakeGoogleConsent(page, (state) => ({ error: 'access_denied', state }))
  await expect(connect).toBeEnabled()
  await connect.click()
  await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('Google sign-in was cancelled')
  await expect(page).toHaveURL(/\/dashboard\/connections$/) // the ?google= note is tidied away
  const asked = google.url
  expect(asked, 'the browser went to Google').not.toBeNull()
  expect(asked?.searchParams.get('client_id')).toBe(CLIENT_ID)
  expect(asked?.searchParams.get('redirect_uri')).toBe(REDIRECT_URI)
  expect(asked?.searchParams.get('scope')).toBe('https://www.googleapis.com/auth/calendar.readonly')
  expect(asked?.searchParams.get('code_challenge_method')).toBe('S256')
  expect(asked?.searchParams.get('code_challenge')).toBeTruthy()
  expect(asked?.searchParams.get('state')).toBeTruthy()
  await expect(googleRow(page)).toContainText('OAuth client saved')
  expect(outside).toEqual([])
})

test('a forged sign-in is refused and nothing is connected', async ({ page, api, owner }) => {
  void owner
  const outside = await blockGoogle(page)
  await api.ok(api.post('/app/connections/google', { client_id: CLIENT_ID, client_secret: CLIENT_SECRET }))
  // Google comes back with a code, but not with the state this app made, so the code is never used.
  await fakeGoogleConsent(page, () => ({ code: 'e2e-fake-code', state: 'forged-state' }))
  await openConnections(page)
  await googleRow(page).click()
  await page.getByRole('button', { name: 'Connect Google Calendar', exact: true }).click()
  await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('expired or was already used')
  await expect(googleRow(page)).not.toContainText('Connected')
  expect((await storedSecrets()).map((s) => s.name)).not.toContain('refresh_token')
  expect(outside).toEqual([])
})

test('once connected, it points to the Google Calendar skill until that is on', async ({ page, owner }) => {
  void owner
  const outside = await blockGoogle(page)
  // The real exchange with Google can't run here: serve the connected status instead.
  await page.route(
    (url) => url.pathname === '/api/app/connections',
    async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.services.google = {
        connected: true,
        via: 'app',
        client_ready: true,
        hints: { client_id: CLIENT_ID, client_secret: 'e2e…1234', refresh_token: '1//…abcd' },
        meta: { scopes: ['https://www.googleapis.com/auth/calendar.readonly'] },
        since: null,
      }
      await route.fulfill({ response, json: body })
    },
  )
  await page.goto('/dashboard/connections?google=connected')
  await expect(page.getByRole('status', { name: 'Google Calendar result' })).toContainText('Google Calendar is connected')
  await expect(page).toHaveURL(/\/dashboard\/connections$/)
  await expect(googleRow(page)).toContainText('Connected')
  await expect(page.getByRole('button', { name: 'Connect Google Calendar', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Disconnect Google Calendar' })).toBeVisible()

  await page.getByRole('link', { name: 'Enable it on the Skills page' }).click()
  await expect(page).toHaveURL(/\/dashboard\/skills$/)
  const calendar = page.getByRole('list', { name: 'Skills' }).getByRole('listitem', { name: 'Google Calendar' })
  await calendar.getByRole('button', { name: 'Install' }).click()
  await page.getByRole('group', { name: 'Permissions for Google Calendar' }).getByRole('button', { name: 'Allow and install' }).click()
  await expect(calendar.getByText('Installed')).toBeVisible()

  await openConnections(page)
  await googleRow(page).click()
  await expect(page.getByText('Signed in with Google')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Enable it on the Skills page' })).toHaveCount(0)
  expect(outside).toEqual([])
})
