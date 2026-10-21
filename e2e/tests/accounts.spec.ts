// Accounts and access: sign-up, log-in, log-out, sessions, redirects, host names,
// CSRF and not-found pages. Each test starts from an empty database.
import type { Page } from '@playwright/test'
import { expect, newPerson, query, test, type Person } from '../src/fixtures'
import { PASSWORD, WEB_PORT } from '../src/env'
import {
  HOSTILE_NEXT,
  MSG,
  collectPageErrors,
  expectFieldError,
  fillLogin,
  fillSignup,
  forgedCookie,
  loginForm,
  sessionCookie,
  signupForm,
  trapOffsiteRequests,
} from '../src/helpers/accounts'

// Open the form and wait until it's live (the first field takes focus once the page has hydrated).
async function gotoSignup(page: Page, next?: string, origin = ''): Promise<void> {
  await page.goto(`${origin}/signup${next === undefined ? '' : `?next=${encodeURIComponent(next)}`}`)
  await expect(signupForm(page).name).toBeFocused()
}

async function gotoLogin(page: Page, next?: string, origin = ''): Promise<void> {
  await page.goto(`${origin}/login${next === undefined ? '' : `?next=${encodeURIComponent(next)}`}`)
  await expect(loginForm(page).email).toBeFocused()
}

// Submit the log-in form and return the API's answer.
async function attemptLogin(page: Page, email: string, password: string): Promise<{ status: number; body: string }> {
  await fillLogin(page, email, password)
  const response = page.waitForResponse((r) => r.url().endsWith('/api/auth/login') && r.request().method() === 'POST')
  await loginForm(page).submit.click()
  const r = await response
  return { status: r.status(), body: await r.text() }
}

async function countAccounts(): Promise<number> {
  return (await query('SELECT 1 FROM user_account')).length
}

const workspaceHeading = (page: Page, person: Person) =>
  page.getByRole('heading', { name: `Hello, ${person.name.split(' ')[0]}. What shall we work on?` })

// ---------------------------------------------------------------------------------
test.describe('sign-up', () => {
  test('a new visitor creates an account and is signed in', async ({ page, api }) => {
    const person = newPerson()
    await gotoSignup(page)
    await expect(page).toHaveTitle(/Create your account/)
    await fillSignup(page, person)
    await signupForm(page).submit.click()

    await expect(page).toHaveURL('/')
    await expect(page.getByRole('navigation').getByRole('button', { name: 'Log out' })).toBeVisible()
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toHaveCount(0)

    // What was stored: one account, the password hashed (never kept as typed).
    const rows = await query<{ email_normalized: string; display_name: string; password_hash: string }>(
      'SELECT email_normalized, display_name, password_hash FROM user_account',
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].email_normalized).toBe(person.email)
    expect(rows[0].display_name).toBe(person.name)
    expect(rows[0].password_hash).toMatch(/^\$argon2id\$/)
    expect(rows[0].password_hash).not.toContain(person.password)

    // The session is an httpOnly, SameSite=Lax cookie, and the API knows who it is.
    const cookie = await sessionCookie(page.context())
    expect(cookie?.httpOnly).toBe(true)
    expect(cookie?.sameSite).toBe('Lax')
    const me = await api.ok<{ user: { email: string; name: string } }>(api.get('/auth/me'))
    expect(me.user).toMatchObject({ email: person.email, name: person.name })
  })

  test('a mixed-case email is stored once, and log-in works with any case', async ({ page, api }) => {
    const person = newPerson()
    const mixed = person.email.replace('e2e-', 'E2E.Mixed-').replace('example.com', 'Example.COM')
    await gotoSignup(page)
    await fillSignup(page, { ...person, email: mixed })
    await signupForm(page).submit.click()
    await expect(page).toHaveURL('/')

    const rows = await query<{ email_normalized: string }>('SELECT email_normalized FROM user_account')
    expect(rows).toEqual([{ email_normalized: mixed.toLowerCase() }])

    await page.context().clearCookies()
    const r = await api.login(`  ${mixed.toLowerCase()}  `, person.password)
    expect(r.status()).toBe(200)
  })

  // Each refused password: the reason sits under the password field, and nothing is stored.
  const passwordCases: { title: string; password: (p: Person) => string; message: string }[] = [
    { title: 'too short', password: () => 'short one', message: MSG.shortPassword },
    { title: 'too long', password: () => 'a long walk '.repeat(25), message: MSG.longPassword },
    { title: 'a common password', password: () => 'password123', message: MSG.commonPassword },
    { title: 'one repeated character', password: () => 'aaaaaaaaaaaa', message: MSG.commonPassword },
    { title: 'contains your email', password: (p) => `my ${p.email.split('@')[0]} secret`, message: MSG.personalPassword },
    {
      title: 'contains your name run together',
      password: (p) => `${p.name.replace(/\s/g, '').toLowerCase()} rules ok`,
      message: MSG.personalPassword,
    },
  ]
  for (const c of passwordCases) {
    test(`a password that is ${c.title} is refused next to the password field`, async ({ page, api }) => {
      void api
      const person = newPerson()
      await gotoSignup(page)
      await fillSignup(page, { ...person, password: c.password(person) })
      // While nothing is wrong, the hint explains the rule.
      await expect(page.getByText(MSG.passwordHint)).toBeVisible()
      await signupForm(page).submit.click()

      await expectFieldError(page, signupForm(page).password, c.message)
      await expect(page.getByText(MSG.passwordHint)).toHaveCount(0)
      await expect(page).toHaveURL(/\/signup$/)
      expect(await countAccounts()).toBe(0)
      expect(await sessionCookie(page.context())).toBeUndefined()
    })
  }

  test('a password that contains your name as you typed it is refused', async ({ page, api }) => {
    test.fail(true, 'BUG: the name check strips spaces from the name but not the password, so "Shashank Rao …" is accepted')
    void api
    const person = newPerson('Shashank Rao')
    await gotoSignup(page)
    await fillSignup(page, { ...person, password: 'Shashank Rao rules ok' })
    const response = page.waitForResponse((r) => r.url().endsWith('/api/auth/signup'))
    await signupForm(page).submit.click()
    expect((await response).status()).toBe(422)
    await expectFieldError(page, signupForm(page).password, MSG.personalPassword)
    expect(await countAccounts()).toBe(0)
  })

  for (const email of ['not-an-email', 'a@b', 'shashank@', '@example.com']) {
    test(`an invalid email (${email}) is refused next to the email field`, async ({ page, api }) => {
      void api
      await gotoSignup(page)
      await fillSignup(page, { ...newPerson(), email })
      await signupForm(page).submit.click()
      await expectFieldError(page, signupForm(page).email, MSG.invalidEmail)
      expect(await countAccounts()).toBe(0)
    })
  }

  test('a very short email gets the same plain message as any other invalid email', async ({ page, api }) => {
    test.fail(true, 'BUG: an email under 3 characters shows the raw validator text "String should have at least 3 characters"')
    void api
    await gotoSignup(page)
    await fillSignup(page, { ...newPerson(), email: 'ab' })
    await signupForm(page).submit.click()
    const alert = page.locator('form').getByRole('alert')
    await expect(alert).toBeVisible()
    await expect(alert).toHaveText(MSG.invalidEmail, { timeout: 1000 })
    await expectFieldError(page, signupForm(page).email, MSG.invalidEmail)
  })

  test('a very long password gets the same plain message as any other too-long password', async ({ page, api }) => {
    test.fail(true, 'BUG: a password over 1024 characters shows the raw validator text "String should have at most 1024 characters"')
    void api
    await gotoSignup(page)
    await fillSignup(page, { ...newPerson(), password: 'a long walk '.repeat(100) })
    await signupForm(page).submit.click()
    const alert = page.locator('form').getByRole('alert')
    await expect(alert).toBeVisible()
    await expect(alert).toHaveText(MSG.longPassword, { timeout: 1000 })
  })

  test('a refused sign-up or log-in never sends the password back', async ({ api }) => {
    test.fail(true, 'BUG: validation errors (e.g. a password over 1024 characters) echo the submitted password in "input"')
    const password = `do not echo me ${'x y z '.repeat(200)}`
    const person = newPerson()
    const responses = [
      await api.post('/auth/signup', { name: person.name, email: person.email, password }),
      await api.post('/auth/login', { email: person.email, password }),
    ]
    for (const r of responses) {
      expect(r.ok()).toBe(false)
      expect(await r.text()).not.toContain('do not echo me')
    }
  })

  test('a duplicate email is refused, whatever its case or spacing', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    const variants = [person.email, person.email.toUpperCase(), `  ${person.email.replace('e2e-', 'E2E-')}  `]
    for (const email of variants) {
      await gotoSignup(page)
      await fillSignup(page, { name: 'Priya Nair', email, password: PASSWORD })
      await signupForm(page).submit.click()
      await expectFieldError(page, signupForm(page).email, MSG.duplicateEmail)
      await expect(page).toHaveURL(/\/signup$/)
    }
    // The browser trims email fields itself, so also send the spaces straight to the API.
    const r = await api.post('/auth/signup', { name: 'Priya Nair', email: `\t ${person.email.toUpperCase()} `, password: PASSWORD })
    expect(r.status()).toBe(409)

    expect(await countAccounts()).toBe(1)
    expect(await sessionCookie(page.context())).toBeUndefined()
  })

  test('the error moves to the field that is wrong now', async ({ page, api }) => {
    const taken = newPerson()
    await api.signup(taken)
    await page.context().clearCookies()

    await gotoSignup(page)
    const f = signupForm(page)
    await fillSignup(page, { name: 'Priya Nair', email: taken.email, password: 'too short' })
    await f.submit.click()
    await expectFieldError(page, f.password, MSG.shortPassword)

    await f.password.fill(PASSWORD)
    await f.submit.click()
    await expectFieldError(page, f.email, MSG.duplicateEmail)
    await expect(page.getByText(MSG.shortPassword)).toHaveCount(0)
    await expect(page.getByText(MSG.passwordHint)).toBeVisible()

    await f.email.fill(newPerson().email)
    await f.submit.click()
    await expect(page).toHaveURL('/')
  })

  test('the password field error is announced as the field’s description', async ({ page, api }) => {
    test.fail(true, 'BUG: the password error is not tied to the field (aria-describedby still points at the hidden hint)')
    void api
    await gotoSignup(page)
    await fillSignup(page, { ...newPerson(), password: 'too short' })
    await signupForm(page).submit.click()
    await expect(page.getByText(MSG.shortPassword)).toBeVisible()
    await expect(signupForm(page).password).toHaveAccessibleDescription(MSG.shortPassword, { timeout: 1000 })
  })

  test('the button waits until every field is filled in', async ({ page, api }) => {
    void api
    await gotoSignup(page)
    const f = signupForm(page)
    await expect(f.submit).toBeDisabled()
    await f.name.fill('   ')
    await f.email.fill(newPerson().email)
    await f.password.fill(PASSWORD)
    await expect(f.submit, 'a name of only spaces is not a name').toBeDisabled()
    await f.name.fill('Shashank Rao')
    await expect(f.submit).toBeEnabled()
    await f.password.fill('')
    await expect(f.submit).toBeDisabled()
  })

  test('show password reveals and hides it again, without sending the form', async ({ page, api }) => {
    void api
    await gotoSignup(page)
    const f = signupForm(page)
    await fillSignup(page, newPerson())
    await expect(f.password).toHaveAttribute('type', 'password')

    await f.showPassword.click()
    await expect(f.password).toHaveAttribute('type', 'text')
    await expect(f.password).toHaveValue(PASSWORD)
    await f.hidePassword.click()
    await expect(f.password).toHaveAttribute('type', 'password')

    await expect(page).toHaveURL(/\/signup$/)
    expect(await countAccounts()).toBe(0)
  })

  test('the whole form can be completed with the keyboard alone', async ({ page, api }) => {
    void api
    const person = newPerson()
    await gotoSignup(page)
    const f = signupForm(page)

    await page.keyboard.type(person.name)
    await page.keyboard.press('Tab')
    await expect(f.email).toBeFocused()
    await page.keyboard.type(person.email)
    await page.keyboard.press('Tab')
    await expect(f.password).toBeFocused()
    await page.keyboard.type(person.password)
    await page.keyboard.press('Tab')
    await expect(f.showPassword).toBeFocused()
    await page.keyboard.press('Space')
    await expect(f.password).toHaveAttribute('type', 'text')
    await expect(f.hidePassword).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(f.submit).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL('/')
    expect(await countAccounts()).toBe(1)
  })

  test('a double-click creates one account and no error', async ({ page, api }) => {
    void api
    await gotoSignup(page)
    await fillSignup(page, newPerson())
    await signupForm(page).submit.dblclick()
    await expect(page).toHaveURL('/')
    await expect(page.getByText(MSG.duplicateEmail)).toHaveCount(0)
    expect(await countAccounts()).toBe(1)
  })

  test('markup in a name is shown as text, never run', async ({ page, api }) => {
    void api
    const dialogs: string[] = []
    page.on('dialog', (d) => {
      dialogs.push(d.message())
      void d.dismiss()
    })
    const name = '<img src=x onerror=alert(1)>'
    await gotoSignup(page, '/agents/ava/hire')
    await fillSignup(page, { ...newPerson(), name })
    await signupForm(page).submit.click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
    await expect(page.getByRole('heading', { name: 'One thing before you start, <img' })).toBeVisible()
    expect(await page.locator('img[src="x"]').count()).toBe(0)
    expect(dialogs).toEqual([])
    expect(await query('SELECT display_name FROM user_account')).toEqual([{ display_name: name }])
  })
})

// ---------------------------------------------------------------------------------
test.describe('log-in', () => {
  test.describe('with an account that has not hired yet', () => {
    test.use({ ownerState: 'account' })

    test('logs in and goes to the marketplace', async ({ page, owner }) => {
      await page.context().clearCookies()
      await gotoLogin(page)
      await expect(page).toHaveTitle(/Log in/)
      await fillLogin(page, owner.email, owner.password)
      await loginForm(page).submit.click()
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('navigation').getByRole('button', { name: 'Log out' })).toBeVisible()
      expect(await sessionCookie(page.context())).toBeDefined()
    })

    test('the email is matched whatever its case', async ({ page, owner }) => {
      await page.context().clearCookies()
      await gotoLogin(page)
      await fillLogin(page, owner.email.toUpperCase(), owner.password)
      await loginForm(page).submit.click()
      await expect(page).toHaveURL('/')
    })

    test('next= takes you to the page you asked for', async ({ page, owner }) => {
      await page.context().clearCookies()
      await gotoLogin(page, '/agents/ava')
      await fillLogin(page, owner.email, owner.password)
      await loginForm(page).submit.click()
      await expect(page).toHaveURL('/agents/ava')
    })

    test('switching between log-in and sign-up keeps next=', async ({ page, owner }) => {
      void owner
      await page.context().clearCookies()
      await gotoLogin(page, '/agents/ava/hire')
      const toSignup = page.getByRole('link', { name: 'Create an account' })
      await expect(toSignup).toHaveAttribute('href', '/signup?next=%2Fagents%2Fava%2Fhire')
      await toSignup.click()
      await expect(page).toHaveURL('/signup?next=%2Fagents%2Fava%2Fhire')
      await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
      await expect(page.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login?next=%2Fagents%2Fava%2Fhire')
    })

    for (const next of HOSTILE_NEXT) {
      test(`log-in never follows next=${next} off the site`, async ({ page, owner }) => {
        const offsite = await trapOffsiteRequests(page.context())
        const dialogs: string[] = []
        page.on('dialog', (d) => {
          dialogs.push(d.message())
          void d.dismiss()
        })
        await page.context().clearCookies()
        await gotoLogin(page, next)
        // The switch link doesn't pass it on either.
        await expect(page.getByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup')
        await fillLogin(page, owner.email, owner.password)
        await loginForm(page).submit.click()

        await expect(page).toHaveURL('/')
        await expect(page.getByRole('navigation').getByRole('button', { name: 'Log out' })).toBeVisible()
        expect(offsite).toEqual([])
        expect(dialogs).toEqual([])
      })
    }
  })

  for (const next of HOSTILE_NEXT) {
    test(`sign-up never follows next=${next} off the site`, async ({ page, api }) => {
      void api
      const offsite = await trapOffsiteRequests(page.context())
      const dialogs: string[] = []
      page.on('dialog', (d) => {
        dialogs.push(d.message())
        void d.dismiss()
      })
      await gotoSignup(page, next)
      await expect(page.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
      await fillSignup(page, newPerson())
      await signupForm(page).submit.click()
      await expect(page).toHaveURL('/')
      expect(offsite).toEqual([])
      expect(dialogs).toEqual([])
    })
  }

  test('an introduced owner logs in and lands in the workspace', async ({ page, owner }) => {
    await page.context().clearCookies()
    await gotoLogin(page)
    await fillLogin(page, owner.email, owner.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL('/workspace')
    await expect(workspaceHeading(page, owner)).toBeVisible()
  })

  test('opening a private page, then logging in, returns you to it', async ({ page, owner }) => {
    await page.context().clearCookies()
    await page.goto('/workspace')
    await expect(page).toHaveURL('/login?next=%2Fworkspace')
    await expect(loginForm(page).email).toBeFocused()
    await fillLogin(page, owner.email, owner.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL('/workspace')
    await expect(workspaceHeading(page, owner)).toBeVisible()
  })

  test('a wrong password and an unknown email get exactly the same answer', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    await gotoLogin(page)
    const wrong = await attemptLogin(page, person.email, 'not the password at all')
    await expect(page.getByText(MSG.badLogin)).toBeVisible()
    const wrongForm = await page.locator('form').innerText()

    await gotoLogin(page)
    const unknown = await attemptLogin(page, `nobody-${Date.now()}@example.com`, 'not the password at all')
    await expect(page.getByText(MSG.badLogin)).toBeVisible()
    const unknownForm = await page.locator('form').innerText()

    expect(unknown).toEqual(wrong)
    expect(unknownForm).toBe(wrongForm)
    await expect(page).toHaveURL(/\/login$/)
    expect(await sessionCookie(page.context())).toBeUndefined()
    // Nothing on the form points at either field: it doesn't say which one was wrong.
    await expect(page.locator('form [aria-invalid="true"]')).toHaveCount(0)
  })

  test('hostile text in the log-in form is just a wrong log-in', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()
    const dialogs: string[] = []
    page.on('dialog', (d) => {
      dialogs.push(d.message())
      void d.dismiss()
    })
    await gotoLogin(page)
    for (const [email, password] of [
      ["' OR '1'='1", "' OR '1'='1"],
      [`${person.email}' --`, 'anything at all'],
      ['<script>alert(1)</script>@example.com', '<img src=x onerror=alert(1)>'],
    ]) {
      const r = await attemptLogin(page, email, password)
      expect(r.status, email).toBe(401)
      await expect(page.getByText(MSG.badLogin)).toBeVisible()
    }
    expect(dialogs).toEqual([])
    expect(await sessionCookie(page.context())).toBeUndefined()
  })

  test('a failed log-in is announced to screen readers', async ({ page, api }) => {
    test.fail(true, "BUG: log-in errors render in a plain <div> (Notice) with no role=alert or live region")
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()
    await gotoLogin(page)
    await attemptLogin(page, person.email, 'not the password at all')
    await expect(page.getByText(MSG.badLogin)).toBeVisible()
    await expect(page.getByRole('alert').filter({ hasText: MSG.badLogin })).toBeVisible({ timeout: 1000 })
  })

  test('after 5 wrong passwords the account is locked, even for the right password', async ({ page, api }) => {
    test.slow() // seven real password checks (Argon2) in a row
    const person = newPerson()
    const other = newPerson('Priya Nair')
    await api.signup(other)
    await api.signup(person)
    await page.context().clearCookies()

    await gotoLogin(page)
    for (let i = 1; i <= 5; i++) {
      const r = await attemptLogin(page, person.email, `wrong guess number ${i}`)
      expect(r.status, `attempt ${i}`).toBe(401)
      await expect(page.getByText(MSG.badLogin)).toBeVisible()
    }
    const locked = await attemptLogin(page, person.email, person.password)
    expect(locked.status).toBe(429)
    await expect(page.getByText(MSG.locked)).toBeVisible()
    await expect(page.getByText(MSG.badLogin)).toHaveCount(0)
    await expect(page).toHaveURL(/\/login$/)
    expect(await sessionCookie(page.context())).toBeUndefined()

    const rows = await query<{ locked: boolean }>(
      'SELECT locked_until > now() + interval \'10 minutes\' AS locked FROM user_account WHERE email_normalized = $1',
      [person.email],
    )
    expect(rows).toEqual([{ locked: true }])

    // Someone else's account is not affected.
    await gotoLogin(page)
    await fillLogin(page, other.email, other.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL('/')
  })

  test('the button is disabled while the API is slow, so one log-in is sent', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    let calls = 0
    let release!: () => void
    const gate = new Promise<void>((resolve) => (release = resolve))
    await page.route('**/api/auth/login', async (route) => {
      calls += 1
      await gate
      await route.continue()
    })

    await gotoLogin(page)
    await fillLogin(page, person.email, person.password)
    await loginForm(page).submit.click()
    const busy = page.getByRole('button', { name: 'Logging in…' })
    await expect(busy).toBeDisabled()
    await loginForm(page).password.press('Enter') // pressing Enter again does nothing
    release()

    await expect(page).toHaveURL('/')
    expect(calls).toBe(1)
    expect(await query("SELECT 1 FROM auth_event WHERE kind = 'login_ok'")).toHaveLength(1)
  })

  test('when the API can’t be reached, the message says so and a retry works', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    await page.route('**/api/auth/login', (route) => route.abort('connectionrefused'))
    await gotoLogin(page)
    await fillLogin(page, person.email, person.password)
    await loginForm(page).submit.click()
    await expect(page.getByText(/Can't reach Superaide/)).toBeVisible()
    await expect(loginForm(page).email).toHaveValue(person.email)
    await expect(loginForm(page).submit).toBeEnabled()

    await page.unroute('**/api/auth/login')
    await loginForm(page).submit.click()
    await expect(page).toHaveURL('/')
  })

  test('a server error shows a message a person can act on', async ({ page, api }) => {
    test.fail(true, 'BUG: a 500 with a plain-text body shows "Request failed with status code 500"')
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    await page.route('**/api/auth/login', (route) =>
      route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' }),
    )
    await gotoLogin(page)
    const status = await attemptLogin(page, person.email, person.password)
    expect(status.status).toBe(500)
    await expect(loginForm(page).submit).toBeEnabled()
    await expect(page.locator('form')).not.toContainText(/status code/i, { timeout: 1000 })
    await expect(page.locator('form')).toContainText(/try again/i, { timeout: 1000 })
  })

  test('the log-in button waits for both fields', async ({ page, api }) => {
    void api
    await gotoLogin(page)
    const f = loginForm(page)
    await expect(f.submit).toBeDisabled()
    await f.email.fill('someone@example.com')
    await expect(f.submit).toBeDisabled()
    await f.password.fill('x')
    await expect(f.submit).toBeEnabled()
  })

  test('show password works on the log-in form too', async ({ page, api }) => {
    void api
    await gotoLogin(page)
    const f = loginForm(page)
    await f.password.fill('secret words here')
    await f.showPassword.click()
    await expect(f.password).toHaveAttribute('type', 'text')
    await page.getByRole('button', { name: 'Hide password' }).click()
    await expect(f.password).toHaveAttribute('type', 'password')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('log-in can be completed with the keyboard alone', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()
    await gotoLogin(page)
    await page.keyboard.type(person.email)
    await page.keyboard.press('Tab')
    await expect(loginForm(page).password).toBeFocused()
    await page.keyboard.type(person.password)
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL('/')
  })
})

// ---------------------------------------------------------------------------------
test.describe('log-out', () => {
  test('from the nav: signed out on the server, and private pages send you to log in', async ({ page, owner }) => {
    void owner
    await page.goto('/')
    const nav = page.getByRole('navigation')
    await expect(nav.getByRole('button', { name: 'Open Ava' })).toBeVisible()
    await nav.getByRole('button', { name: 'Log out' }).click()

    await expect(nav.getByRole('link', { name: 'Log in' })).toBeVisible()
    await expect(nav.getByRole('button', { name: 'Log out' })).toHaveCount(0)
    await expect(page).toHaveURL('/')
    expect(await sessionCookie(page.context())).toBeUndefined()
    const sessions = await query<{ revoked: boolean }>('SELECT revoked_at IS NOT NULL AS revoked FROM user_session')
    expect(sessions).toEqual([{ revoked: true }])

    for (const path of ['/workspace', '/meet']) {
      await page.goto(path)
      await expect(page).toHaveURL(`/login?next=${encodeURIComponent(path)}`)
    }
    await page.goto('/agents/ava/hire')
    await expect(page).toHaveURL('/signup?next=%2Fagents%2Fava%2Fhire')
  })

  test('from the workspace bar', async ({ page, owner, api }) => {
    await page.goto('/workspace')
    await expect(workspaceHeading(page, owner)).toBeVisible()
    await page.getByRole('button', { name: 'Log out' }).click()
    await expect(page).not.toHaveURL(/\/workspace$/)
    await expect(workspaceHeading(page, owner)).toHaveCount(0)
    expect((await api.get('/auth/me')).status()).toBe(401)
  })

  for (const how of ['an in-app link', 'a full page load'] as const) {
    test(`the back button never shows private data again (left the workspace by ${how})`, async ({ page, owner }) => {
      // Watch the page for the workspace greeting from the moment we log out.
      await page.addInitScript(() => {
        const check = () => {
          if (sessionStorage.getItem('e2e-armed') && document.body?.innerText.includes('What shall we work on?')) {
            sessionStorage.setItem('e2e-leak', location.pathname)
          }
        }
        new MutationObserver(check).observe(document, { subtree: true, childList: true, characterData: true })
        document.addEventListener('DOMContentLoaded', check)
      })
      await page.goto('/workspace')
      await expect(workspaceHeading(page, owner)).toBeVisible()
      if (how === 'an in-app link') await page.getByRole('link', { name: 'Superaide' }).click()
      else await page.goto('/')
      await expect(page).toHaveURL('/')

      await page.getByRole('navigation').getByRole('button', { name: 'Log out' }).click()
      await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()
      await page.evaluate(() => sessionStorage.setItem('e2e-armed', '1'))

      await page.goBack()
      await expect(page).toHaveURL('/login?next=%2Fworkspace')
      await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()
      await expect(workspaceHeading(page, owner)).toHaveCount(0)
      expect(await page.evaluate(() => sessionStorage.getItem('e2e-leak'))).toBeNull()
    })
  }

  test('the old session cookie can’t be replayed after log-out', async ({ page, owner, api }) => {
    void owner
    const old = await sessionCookie(page.context())
    expect(old).toBeDefined()
    await page.goto('/')
    await page.getByRole('navigation').getByRole('button', { name: 'Log out' }).click()
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()

    await page.context().addCookies([old!])
    expect((await api.get('/auth/me')).status()).toBe(401)
    expect((await api.get('/app/me')).status()).toBe(401)
    expect((await api.get('/app/messages')).status()).toBe(401)
    expect((await api.post('/app/chat', { text: 'Are you still there?' })).status()).toBe(401)

    await page.goto('/workspace')
    await expect(page).toHaveURL('/login?next=%2Fworkspace')
  })

  test('logging out and back in picks up where you left off', async ({ page, owner }) => {
    await page.goto('/')
    await page.getByRole('navigation').getByRole('button', { name: 'Log out' }).click()
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()
    await page.getByRole('navigation').getByRole('link', { name: 'Log in' }).click()
    await expect(page).toHaveURL('/login')
    await expect(loginForm(page).email).toBeFocused()
    await fillLogin(page, owner.email, owner.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL('/workspace')
    await expect(workspaceHeading(page, owner)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------------
test.describe('sessions', () => {
  test('a session survives a reload and a new page in the same browser', async ({ page, owner }) => {
    await page.goto('/workspace')
    await expect(workspaceHeading(page, owner)).toBeVisible()
    await page.reload()
    await expect(workspaceHeading(page, owner)).toBeVisible()

    const second = await page.context().newPage()
    await second.goto('/workspace')
    await expect(workspaceHeading(second, owner)).toBeVisible()
    await second.close()
  })

  test('another browser is not signed in', async ({ page, owner, browser }) => {
    void owner
    const origin = new URL(test.info().project.use.baseURL ?? page.url()).origin
    const other = await browser.newContext()
    const p = await other.newPage()
    await p.goto(`${origin}/workspace`)
    await expect(p).toHaveURL(`${origin}/login?next=%2Fworkspace`)
    await other.close()
  })

  test.describe('a signed-in visitor who opens log-in or sign-up is sent on', () => {
    test.use({ ownerState: 'account' })
    test('to the marketplace, or to next=', async ({ page, owner }) => {
      void owner
      for (const path of ['/login', '/signup']) {
        await page.goto(path)
        await expect(page).toHaveURL('/')
      }
      await page.goto('/login?next=%2Fagents%2Fava')
      await expect(page).toHaveURL('/agents/ava')
    })
  })

  test('an introduced owner opening log-in or sign-up goes to the workspace', async ({ page, owner }) => {
    for (const path of ['/login', '/signup']) {
      await page.goto(path)
      await expect(page).toHaveURL('/workspace')
      await expect(workspaceHeading(page, owner)).toBeVisible()
    }
  })

  const forged = {
    garbage: 'garbage',
    'token-shaped': 'Q2hlY2tpbmctdGhhdC1hLXJhbmRvbS10b2tlbi1mYWls',
    'very long': 'x'.repeat(3500),
  }
  for (const [kind, value] of Object.entries(forged)) {
    test(`a forged session cookie (${kind}) counts as signed out, without a crash`, async ({ page, api }) => {
      const errors = collectPageErrors(page)
      const origin = new URL(test.info().project.use.baseURL!).origin
      await page.context().addCookies([forgedCookie(value, origin)])

      expect((await api.get('/auth/me')).status()).toBe(401)
      await page.goto('/workspace')
      await expect(page).toHaveURL('/login?next=%2Fworkspace')
      await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible()

      await page.goto('/')
      await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()

      // And a real log-in replaces it.
      const person = newPerson()
      await api.signup(person)
      await page.context().clearCookies()
      await page.context().addCookies([forgedCookie(value, origin)])
      await gotoLogin(page)
      await fillLogin(page, person.email, person.password)
      await loginForm(page).submit.click()
      await expect(page).toHaveURL('/')
      expect((await sessionCookie(page.context()))?.value).not.toBe(value)
      expect(errors).toEqual([])
    })
  }

  test('an expired session counts as signed out', async ({ page, owner, api }) => {
    void owner
    await query("UPDATE user_session SET expires_at = now() - interval '1 minute'")
    expect((await api.get('/auth/me')).status()).toBe(401)
    await page.goto('/workspace')
    await expect(page).toHaveURL('/login?next=%2Fworkspace')
  })
})

// ---------------------------------------------------------------------------------
test.describe('redirects when signed out', () => {
  const cases = [
    { path: '/meet', to: '/login?next=%2Fmeet', heading: 'Welcome back' },
    { path: '/workspace', to: '/login?next=%2Fworkspace', heading: 'Welcome back' },
    { path: '/agents/ava/hire', to: '/signup?next=%2Fagents%2Fava%2Fhire', heading: 'Create your account' },
  ]
  for (const c of cases) {
    test(`${c.path} sends you to ${c.to.split('?')[0]} and back`, async ({ page, api }) => {
      void api
      await page.goto(c.path)
      await expect(page).toHaveURL(c.to)
      await expect(page.getByRole('heading', { name: c.heading })).toBeVisible()
    })
  }

  test('hiring while signed out: the sign-up page shows the hiring steps', async ({ page, api }) => {
    void api
    await page.goto('/agents/ava/hire')
    await expect(page.getByLabel('Hiring progress: step 1 of 3')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Back' })).toHaveAttribute('href', '/agents/ava')
  })
})

// ---------------------------------------------------------------------------------
test.describe('host names', () => {
  const origin = `http://127.0.0.1:${WEB_PORT}`

  test('opened as 127.0.0.1: sign up, then hire, and the session sticks', async ({ page, api }) => {
    void api
    const person = newPerson()
    await page.goto(`${origin}/agents/ava`)
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page).toHaveURL(`${origin}/signup?next=%2Fagents%2Fava%2Fhire`)
    await expect(signupForm(page).name).toBeFocused()
    await fillSignup(page, person)
    await signupForm(page).submit.click()

    await expect(page).toHaveURL(`${origin}/agents/ava/hire`)
    await expect(page.getByText(`Signed in as ${person.email}`)).toBeVisible()
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page.getByRole('heading', { name: 'Ava is on your team' })).toBeVisible()
    expect(await query('SELECT agent_slug FROM agent_hire')).toEqual([{ agent_slug: 'ava' }])

    const cookie = await sessionCookie(page.context())
    expect(cookie?.domain).toBe('127.0.0.1')
    await page.reload()
    await expect(page).toHaveURL(`${origin}/meet`)
  })

  test('opened as 127.0.0.1: log in and reach the workspace', async ({ page, owner }) => {
    await page.context().clearCookies()
    await page.goto(`${origin}/workspace`)
    await expect(page).toHaveURL(`${origin}/login?next=%2Fworkspace`)
    await expect(loginForm(page).email).toBeFocused()
    await fillLogin(page, owner.email, owner.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL(`${origin}/workspace`)
    await expect(workspaceHeading(page, owner)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------------
test.describe('CSRF', () => {
  test('sign-up and log-in without X-Requested-With are refused', async ({ page, api }) => {
    const person = newPerson()
    const signup = await api.postWithoutCsrfHeader('/auth/signup', person)
    expect(signup.status()).toBe(403)
    expect(await countAccounts()).toBe(0)
    expect(await sessionCookie(page.context())).toBeUndefined()

    await api.signup(person)
    await page.context().clearCookies()
    const login = await api.postWithoutCsrfHeader('/auth/login', { email: person.email, password: person.password })
    expect(login.status()).toBe(403)
    expect(await sessionCookie(page.context())).toBeUndefined()
  })

  test.describe('signed in', () => {
    test.use({ ownerState: 'account' })
    test('log-out and hire without X-Requested-With are refused and change nothing', async ({ owner, api }) => {
      void owner
      expect((await api.postWithoutCsrfHeader('/auth/logout')).status()).toBe(403)
      expect((await api.get('/auth/me')).status()).toBe(200)
      expect(await query('SELECT 1 FROM user_session WHERE revoked_at IS NOT NULL')).toHaveLength(0)

      expect((await api.postWithoutCsrfHeader('/app/hire', { agent: 'ava', accepted_notice: true })).status()).toBe(403)
      expect(await query('SELECT 1 FROM agent_hire')).toHaveLength(0)
    })

    test('another website can’t log you out or hire with your cookie', async ({ page, owner, api }) => {
      void owner
      const target = new URL(test.info().project.use.baseURL!).origin
      // A stand-in for a hostile site: served by the test, nothing leaves the machine.
      await page.route('http://evil.example/**', (route) =>
        route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>evil</title><p>Free prizes</p>' }),
      )
      await page.goto('http://evil.example/')
      const results = await page.evaluate(async (base) => {
        const tryIt = async (path: string, init: RequestInit) => {
          try {
            const r = await fetch(base + path, { method: 'POST', credentials: 'include', ...init })
            return `status ${r.status}`
          } catch {
            return 'blocked'
          }
        }
        return {
          withHeader: await tryIt('/api/auth/logout', { headers: { 'X-Requested-With': 'XMLHttpRequest' } }),
          hireWithHeader: await tryIt('/api/app/hire', {
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json' },
            body: JSON.stringify({ agent: 'ava', accepted_notice: true }),
          }),
          // A plain form-style post can't add the header, so the API refuses it.
          plain: await tryIt('/api/auth/logout', { mode: 'no-cors' }),
        }
      }, target)
      expect(results.withHeader).toBe('blocked')
      expect(results.hireWithHeader).toBe('blocked')

      expect((await api.get('/auth/me')).status()).toBe(200)
      expect(await query('SELECT 1 FROM user_session WHERE revoked_at IS NOT NULL')).toHaveLength(0)
      expect(await query('SELECT 1 FROM agent_hire')).toHaveLength(0)
    })
  })
})

// ---------------------------------------------------------------------------------
test.describe('not-found pages', () => {
  for (const path of ['/nope', '/nope/deeper/still', '/agents/nobody']) {
    test(`${path} shows the site's 404 page with a way back`, async ({ page, api }) => {
      void api
      const errors = collectPageErrors(page)
      const response = await page.goto(path)
      expect(response?.status()).toBe(404)
      await expect(page.getByRole('heading', { name: "There's no page here" })).toBeVisible()
      const back = page.getByRole('link', { name: 'See all agents' })
      await back.click()
      await expect(page).toHaveURL('/')
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
      expect(errors).toEqual([])
    })
  }

  test.describe('signed in', () => {
    test.use({ ownerState: 'account' })
    test('hiring an agent that does not exist shows the 404 page', async ({ page, owner }) => {
      test.fail(true, 'BUG: /agents/nobody/hire renders a "Hire Ava" page (200) for an agent that does not exist')
      void owner
      const response = await page.goto('/agents/nobody/hire')
      expect(response?.status()).toBe(404)
      await expect(page.getByRole('heading', { name: "There's no page here" })).toBeVisible()
    })
  })
})
