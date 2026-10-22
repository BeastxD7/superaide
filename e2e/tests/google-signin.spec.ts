// "Continue with Google" on the log-in and sign-up pages, against a fake Google
// (scripts/fake-google.mjs). Muted unless E2E_GOOGLE=1, which starts the fake and switches
// Google sign-in on in the API:
//
//   E2E_SLOT=51 E2E_GOOGLE=1 npx playwright test tests/google-signin.spec.ts --project=desktop
import { expect, newPerson, query, test } from '../src/fixtures'
import { FAKE_GOOGLE_URL, GOOGLE } from '../src/env'

test.skip(!GOOGLE, 'muted: run with E2E_GOOGLE=1 (starts a fake Google)')

const googleButton = (page: import('@playwright/test').Page) => page.getByRole('button', { name: 'Continue with Google' })

test('the log-in page offers Google with its G mark, and asks Google only who you are', async ({ page, api }) => {
  void api // fresh database
  await page.goto('/login')
  await expect(googleButton(page)).toBeVisible()
  await expect(googleButton(page).locator('svg')).toHaveCount(1) // the Google "G"
  await googleButton(page).click()
  await expect(page).toHaveURL(new RegExp(`^${FAKE_GOOGLE_URL}/o/oauth2/v2/auth\\?`))
  const url = new URL(page.url())
  expect(url.searchParams.get('scope')).toBe('openid email profile')
  expect(url.searchParams.get('code_challenge_method')).toBe('S256')
  expect(url.searchParams.get('nonce')).toBeTruthy()
  expect(url.searchParams.get('redirect_uri')).toMatch(/\/api\/auth\/google\/callback$/)
})

test('a new person signs up with Google and lands signed in, email already verified', async ({ page, api }) => {
  void api
  await page.goto('/signup')
  await googleButton(page).click()
  await page.getByRole('link', { name: 'Continue as Gita Google' }).click()
  await expect(page).toHaveURL(/\/$/) // home for an account that hasn't hired yet
  const me = await page.request.get('/api/auth/me')
  expect(me.status()).toBe(200)
  const account = (await me.json()) as { user: { email: string; email_verified: boolean; has_password: boolean } }
  expect(account.user).toMatchObject({ email: 'e2e-google@example.com', email_verified: true, has_password: false })
  const rows = await query<{ google_sub: string }>('SELECT google_sub FROM user_account WHERE email_normalized = $1', [
    'e2e-google@example.com',
  ])
  expect(rows[0]?.google_sub).toBe('fake-e2e-google@example.com')
})

test('Google sign-in goes back to where you were heading', async ({ page, api }) => {
  void api
  await page.goto(`/signup?next=${encodeURIComponent('/agents/ava/hire')}`)
  await googleButton(page).click()
  await page.getByRole('link', { name: 'Continue as Gita Google' }).click()
  await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
})

test('cancelling on Google comes back to log in with a plain message, signed out', async ({ page, api }) => {
  void api
  await page.goto('/login')
  await googleButton(page).click()
  await page.getByRole('link', { name: 'Cancel' }).click()
  await expect(page).toHaveURL(/\/login\?google=cancelled$/)
  await expect(page.getByText('Google sign-in was cancelled')).toBeVisible()
  expect((await page.request.get('/api/auth/me')).status()).toBe(401)
})

test('a sign-in link opened in another browser does not sign anyone in', async ({ page, browser, api }) => {
  void api
  await page.goto('/login')
  await googleButton(page).click()
  // Someone else's browser (no cookie from this one's start) finishes the sign-in.
  const finish = await page.getByRole('link', { name: 'Continue as Gita Google' }).getAttribute('href')
  expect(finish).toBeTruthy()
  const other = await browser.newContext()
  const r = await other.request.get(finish as string, { maxRedirects: 0 })
  expect(r.status()).toBe(303)
  expect(r.headers()['location']).toMatch(/\/login\?google=expired$/)
  expect(r.headers()['set-cookie'] ?? '').not.toContain('superaide_session=')
  await other.close()
})

test('the password form still works next to Google', async ({ page, api }) => {
  const person = newPerson()
  await api.signup(person)
  await api.post('/auth/logout')
  await page.goto('/login')
  await expect(googleButton(page)).toBeVisible()
  await page.getByLabel('Email').fill(person.email)
  await page.getByLabel('Password', { exact: true }).fill(person.password)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page).not.toHaveURL(/\/login/)
})
