// QA (accounts): changing your email, the emailed links (verify, reset), sessions that end while a
// page is open, Sign in with Google edge cases, and the account menu.
//
// Google tests need the fake Google server: run with E2E_GOOGLE=1 (they skip otherwise).
// One test reads the API's log for the emailed link: set QA_API_LOG to the file the slot's API
// writes its stderr to (it skips otherwise).
//
//   E2E_SLOT=1 E2E_GOOGLE=1 npx playwright test tests/qa-accounts.spec.ts --project=desktop
import { createHash, randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'
import type { Page } from '@playwright/test'
import { expect, newPerson, query, test, type Person } from '../src/fixtures'
import { GOOGLE, WEB_PORT } from '../src/env'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/accounts`
const shot = (page: Page, name: string) => page.screenshot({ path: `${QA}/${name}.png`, fullPage: true })

// An emailed link, as the server would make it: a random token whose SHA-256 is stored.
async function emailLink(email: string, purpose: 'verify_email' | 'reset_password'): Promise<string> {
  const token = randomBytes(32).toString('base64url')
  const hash = createHash('sha256').update(token).digest('hex')
  await query(
    `INSERT INTO email_token (id, user_id, purpose, email_normalized, token_hash, expires_at, created_at)
     SELECT gen_random_uuid(), id, $2, email_normalized, $3, now() + interval '30 minutes', now()
     FROM user_account WHERE email_normalized = $1`,
    [email.toLowerCase(), purpose, hash],
  )
  return token
}

// The (first-created) owner's stored address.
async function storedEmail(): Promise<{ email: string; verified: boolean }> {
  const rows = await query<{ email: string; email_verified_at: Date | null }>(
    'SELECT email, email_verified_at FROM user_account ORDER BY created_at LIMIT 1',
  )
  return { email: rows[0]!.email, verified: rows[0]!.email_verified_at !== null }
}

const emailForm = (page: Page) => page.getByRole('form', { name: 'Change email' })

async function changeEmailInSettings(page: Page, email: string, password: string): Promise<number> {
  await page.goto('/dashboard/settings')
  const form = emailForm(page)
  await form.getByLabel('New email').fill(email)
  await form.getByLabel('Password').fill(password)
  const res = page.waitForResponse((r) => r.url().endsWith('/api/auth/email') && r.request().method() === 'POST')
  await form.getByRole('button', { name: 'Change email' }).click()
  return (await res).status()
}

const sidebarLink = (page: Page, href: string) => page.locator(`[data-sidebar="menu-button"][href="${href}"]`)

async function googleAs(page: Page) {
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await page.getByRole('link', { name: 'Continue as Gita Google' }).click()
}

// ---------------------------------------------------------------------------------
test.describe('changing your email', () => {
  test('works from Settings: the new address is shown everywhere and logs in; the old one no longer does', async ({ page, owner, api }) => {
    const moved = `moved-${owner.email}`
    expect(await changeEmailInSettings(page, moved, owner.password)).toBe(200)
    await expect(emailForm(page).getByText(`Your email is now ${moved}.`)).toBeVisible()
    await expect(page.getByText(`You sign in with ${moved}.`)).toBeVisible()
    expect(await storedEmail()).toEqual({ email: moved, verified: false })

    await api.post('/auth/logout')
    expect((await api.login(owner.email, owner.password)).status()).toBe(401)
    expect((await api.login(moved, owner.password)).status()).toBe(200)
  })

  test('is refused with a reason: wrong password, the same address, another account’s address', async ({ page, owner, api }) => {
    const other = newPerson('Priya Shah')
    // Another account already uses this address.
    await api.post('/auth/logout')
    await api.signup(other)
    await api.post('/auth/logout')
    await api.login(owner.email, owner.password)

    expect(await changeEmailInSettings(page, `x-${owner.email}`, 'not my password at all')).toBe(401)
    await expect(emailForm(page).getByText("Your password isn't right.")).toBeVisible()
    expect(await changeEmailInSettings(page, owner.email.toUpperCase(), owner.password)).toBe(422)
    await expect(emailForm(page).getByText("That's already your email.")).toBeVisible()
    expect(await changeEmailInSettings(page, other.email, owner.password)).toBe(409)
    await expect(emailForm(page).getByText("There's already an account with this email.")).toBeVisible()
    expect((await storedEmail()).email).toBe(owner.email)
  })

  test('a verification link sent to the old address stops working after the change', async ({ page, owner, api }) => {
    const old = await emailLink(owner.email, 'verify_email')
    await api.ok(api.post('/auth/email', { email: `moved-${owner.email}`, password: owner.password }))
    await page.goto(`/verify-email#token=${old}`)
    await expect(page.getByText('This link has expired or was already used. Ask for a new one.')).toBeVisible()
    expect((await storedEmail()).verified).toBe(false)
  })

  test('when the hourly email limit refuses the change, the email is not changed', async ({ page, owner, api }) => {
    // Sign-up sent one link; four resends reach the limit of five an hour.
    for (let i = 0; i < 4; i++) expect((await api.post('/auth/verify-email/send')).status()).toBe(200)
    const moved = `moved-${owner.email}`
    expect(await changeEmailInSettings(page, moved, owner.password)).toBe(429)
    await expect(emailForm(page).getByText("We've sent several emails already.", { exact: false })).toBeVisible()
    await shot(page, 'change-email-429-told-it-failed')
    await page.reload()
    await expect(page.getByText(/^You sign in with /)).toBeVisible()
    await shot(page, 'change-email-429-after-reload')
    await expect(page.getByText(`You sign in with ${owner.email}.`)).toBeVisible({ timeout: 2_000 })
    expect((await storedEmail()).email).toBe(owner.email)
  })

  test('the link for the new address points at the address the person is using', async ({ page, owner }) => {
    const log = process.env.QA_API_LOG
    test.skip(!log, 'set QA_API_LOG to the API log file to read the emailed link')
    const origin = `http://127.0.0.1:${WEB_PORT}`
    const headers = { 'X-Requested-With': 'superaide-e2e', Origin: origin }
    await page.request.post(`${origin}/api/auth/login`, { headers, data: { email: owner.email, password: owner.password } })
    const before = readFileSync(log!, 'utf8').length
    const moved = `moved-${owner.email}`
    expect((await page.request.post(`${origin}/api/auth/email`, { headers, data: { email: moved, password: owner.password } })).status()).toBe(200)
    let links: string[] = []
    await expect
      .poll(() => (links = readFileSync(log!, 'utf8').slice(before).match(/https?:\/\/\S+\/verify-email#token=\S+/g) ?? []).length)
      .toBeGreaterThan(0)
    expect(links[0]).toMatch(new RegExp(`^${origin}/verify-email#`))
  })
})

// ---------------------------------------------------------------------------------
test.describe('emailed links', () => {
  test('a verification link confirms the address once, signed out', async ({ page, owner, api }) => {
    await api.post('/auth/logout')
    const token = await emailLink(owner.email, 'verify_email')
    await page.goto(`/verify-email#token=${token}`)
    await expect(page.getByText('Your email address is confirmed. Thank you.')).toBeVisible()
    await expect(page).toHaveURL(/\/verify-email$/) // the token is cleared from the address bar
    expect((await storedEmail()).verified).toBe(true)
    await page.goto('/') // a fresh page load, as opening the email link again would be
    await page.goto(`/verify-email#token=${token}`)
    await expect(page.getByText('This link has expired or was already used.', { exact: false })).toBeVisible()
  })

  test('a reset link sets a new password; the old one stops working', async ({ page, owner, api }) => {
    await api.post('/auth/logout')
    const token = await emailLink(owner.email, 'reset_password')
    await page.goto(`/reset-password#token=${token}`)
    await page.getByLabel('New password').fill('a quiet harbour at dawn')
    await page.getByRole('button', { name: 'Save new password' }).click()
    await expect(page.getByText('Your password is changed and every device was signed out.')).toBeVisible()
    expect((await api.login(owner.email, owner.password)).status()).toBe(401)
    expect((await api.login(owner.email, 'a quiet harbour at dawn')).status()).toBe(200)
  })

  test('the forgot-password form answers the same for an unknown address', async ({ page, api }) => {
    void api
    await page.goto('/forgot-password')
    await page.getByLabel('Email').fill('nobody-here@example.com')
    await page.getByRole('button', { name: 'Email me a link' }).click()
    await expect(page.getByText("If that address has an account, we've emailed a link to reset the password.")).toBeVisible()
  })

  test('a reset link still works after reloading the page once a weak password was refused', async ({ page, owner, api }) => {
    await api.post('/auth/logout')
    const token = await emailLink(owner.email, 'reset_password')
    await page.goto(`/reset-password#token=${token}`)
    await page.getByLabel('New password').fill('short')
    await page.getByRole('button', { name: 'Save new password' }).click()
    await expect(page.getByText('Use at least 10 characters.')).toBeVisible()
    await page.reload()
    await expect(page.getByText('This link is missing its code.', { exact: false }).or(page.getByLabel('New password'))).toBeVisible()
    await shot(page, 'reset-link-lost-on-reload')
    await expect(page.getByLabel('New password')).toBeVisible({ timeout: 2_000 })
    // The link really is still good: the server accepts it.
    const r = await api.post('/auth/password-reset', { token, password: 'a quiet harbour at dawn' })
    expect(r.status()).toBe(200)
  })

  test('after resetting the password in a signed-in browser, "Log in" opens the log-in page', async ({ page, owner }) => {
    await page.goto('/dashboard')
    await expect(sidebarLink(page, '/dashboard/notebook')).toBeVisible()
    const token = await emailLink(owner.email, 'reset_password')
    await page.goto(`/reset-password#token=${token}`)
    await page.getByLabel('New password').fill('a quiet harbour at dawn')
    await page.getByRole('button', { name: 'Save new password' }).click()
    await expect(page.getByText('every device was signed out', { exact: false })).toBeVisible()
    await page.getByRole('link', { name: 'Log in' }).click()
    await expect(page.getByRole('heading', { name: 'Welcome back' }).or(page.getByText('Please log in.').first())).toBeVisible()
    await shot(page, 'reset-then-log-in-lands-on-dashboard')
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible({ timeout: 2_000 })
  })
})

// ---------------------------------------------------------------------------------
test.describe('a session that ends while a page is open', () => {
  for (const how of ['signed out on another device', 'expired'] as const) {
    test(`(${how}) the dashboard sends you to log in on your next click`, async ({ page, owner }) => {
      test.fail(true, 'BUG: dashboard pages show "Please log in." with no way to log in; nothing handles a 401 from /app/*')
      void owner
      await page.goto('/dashboard')
      await expect(sidebarLink(page, '/dashboard/notebook')).toBeVisible()
      await query(how === 'expired' ? "UPDATE user_session SET expires_at = now() - interval '1 minute'" : 'UPDATE user_session SET revoked_at = now()')
      await sidebarLink(page, '/dashboard/notebook').click()
      await expect(page.getByText('Please log in.').first().or(page.getByRole('heading', { name: 'Welcome back' }))).toBeVisible()
      await shot(page, `session-ended-dashboard-${how === 'expired' ? 'expired' : 'revoked'}`)
      await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%2Fnotebook$/, { timeout: 3_000 })
    })
  }
})

// ---------------------------------------------------------------------------------
test.describe('the log-in page and its ?google= reason', () => {
  for (const mode of ['login', 'signup'] as const) {
    test(`/${mode}?google=__proto__ still shows the form with the general message`, async ({ page, api }) => {
      void api
      const errors: string[] = []
      page.on('pageerror', (e) => errors.push(e.message))
      await page.goto(`/${mode}?google=__proto__`)
      await expect(page.getByRole('button', { name: /^(Log in|Create account)$/ }).or(page.getByText('This page couldn’t load'))).toBeVisible()
      await shot(page, `google-proto-${mode}-crash`)
      await expect(page.getByText("We couldn't sign you in with Google.", { exact: false })).toBeVisible({ timeout: 2_000 })
      expect(errors).toEqual([])
    })
  }

  test('an unknown reason (constructor) shows the general message, not an empty box', async ({ page, api }) => {
    void api
    await page.goto('/login?google=constructor')
    await expect(page.getByRole('button', { name: 'Log in', exact: true })).toBeVisible()
    await expect(page.getByText("We couldn't sign you in with Google.", { exact: false })).toBeVisible({ timeout: 2_000 })
  })

  test('a known reason shows its message', async ({ page, api }) => {
    void api
    await page.goto('/login?google=unverified')
    await expect(page.getByText("Google hasn't verified that account's email address", { exact: false })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------------
test.describe('Sign in with Google', () => {
  test.skip(!GOOGLE, 'muted: run with E2E_GOOGLE=1 (starts a fake Google)')

  test('signing in again with Google finds the same account, even after its email changed', async ({ page, api }) => {
    void api
    await page.goto('/signup')
    await googleAs(page)
    await expect(page).toHaveURL(/\/$/)
    // A Google-only account has no password, so change the address directly.
    await query("UPDATE user_account SET email = 'gita-moved@example.com', email_normalized = 'gita-moved@example.com'")
    await page.context().clearCookies()
    await page.goto('/login')
    await googleAs(page)
    await expect(page).not.toHaveURL(/\/login/)
    expect((await query('SELECT 1 FROM user_account')).length).toBe(1)
  })

  test('cancelling on Google keeps where you were heading', async ({ page, api }) => {
    void api
    await page.goto(`/signup?next=${encodeURIComponent('/agents/ava/hire')}`)
    await page.getByRole('button', { name: 'Continue with Google' }).click()
    await page.getByRole('link', { name: 'Cancel' }).click()
    await expect(page.getByText('Google sign-in was cancelled', { exact: false })).toBeVisible()
    await shot(page, 'google-cancel-loses-next')
    await expect(page).toHaveURL(/next=%2Fagents%2Fava%2Fhire/, { timeout: 2_000 })
  })

  test('opened as 127.0.0.1 (as the log-in tests do), Google sign-in works', async ({ page, api }) => {
    void api
    const origin = `http://127.0.0.1:${WEB_PORT}`
    await page.goto(`${origin}/login`)
    await googleAs(page)
    await expect(page).toHaveURL(/\/login\?google=expired$|\/$/)
    await shot(page, 'google-from-127-expired')
    expect((await page.request.get(`${origin}/api/auth/me`)).status()).toBe(200)
  })

  test('an owner who signs in only with Google is not told their password is wrong when changing email', async ({ page, api }) => {
    await page.goto('/signup')
    await googleAs(page)
    await expect(page).toHaveURL(/\/$/)
    await api.hire()
    await api.completeIntroduction()
    const status = await changeEmailInSettings(page, 'gita-new@example.com', 'anything I could think of')
    expect(status).toBe(400) // refused, but not as a wrong password
    await expect(emailForm(page).getByText(/./).last()).toBeVisible()
    await shot(page, 'google-only-change-email')
    await expect(emailForm(page).getByText("Your password isn't right.")).toHaveCount(0, { timeout: 1_000 })
    await expect(emailForm(page)).toContainText(/Google/)
  })
})

// ---------------------------------------------------------------------------------
test.describe('the account menu', () => {
  test('shows the name and email, and Settings opens from the dashboard menu', async ({ page, owner }) => {
    await page.goto('/dashboard')
    const trigger = page.getByRole('button', { name: `Account: signed in as ${owner.name}` })
    await trigger.click()
    const menu = page.getByRole('menu')
    await expect(menu).toContainText(owner.email)
    await menu.getByRole('menuitem', { name: 'Settings' }).click()
    await expect(page).toHaveURL(/\/dashboard\/settings$/)
  })

  test('the menu opens and closes with the keyboard, and Escape returns focus to the button', async ({ page, owner }) => {
    void owner
    await page.goto('/')
    const button = page.getByRole('navigation').getByRole('button', { name: /^Account: signed in as / })
    await button.focus()
    await page.keyboard.press('Enter')
    await expect(button).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByRole('navigation').getByRole('button', { name: 'Log out' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(button).toHaveAttribute('aria-expanded', 'false')
    await expect(button).toBeFocused()
  })

  test('a name that starts with an emoji shows the whole emoji as the initial', async ({ page, api }) => {
    const p: Person = newPerson('😀 Sam Lee')
    await api.signup(p)
    await page.goto('/')
    const button = page.getByRole('navigation').getByRole('button', { name: /^Account: signed in as / })
    await expect(button).toBeVisible()
    await page.getByRole('navigation').screenshot({ path: `${QA}/emoji-initial.png` })
    await expect(button).toHaveText('😀', { timeout: 2_000 })
  })
})
