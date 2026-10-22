// QA pass on hiring: the marketplace, Ava's profile, the hire page, /meet, finishing and coming back.
// Works-as-expected checks plus known bugs (test.fail) found on 22 October 2026.
import type { Page } from '@playwright/test'
import { expect, newPerson, query, test, type Api } from '../src/fixtures'
import { fillLogin, fillSignup, loginForm, signupForm } from '../src/helpers/accounts'
import { Meet } from '../src/helpers/introduction'
import { callDialog, CALL_QUESTIONS, openIntroduction, quickVoice, startCall, typeAnswer } from '../src/helpers/intro-sources'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/hiring`
test.use({ contextOptions: { reducedMotion: 'reduce' } })

const ME_DOWN = { status: 503, contentType: 'application/json', body: '{"detail":"Service unavailable"}' }

// Everything up to the last step ("Connect"), through the API.
async function upToConnect(api: Api): Promise<void> {
  await api.ok(api.get('/app/onboarding'))
  await api.answer('hello', { choice: 'Nice to meet you' })
  await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
  await api.answer('people', { skip: true })
  await api.answer('urgent', { topics: ['A family emergency'] })
  await api.answer('reach', { quiet_start: '22:00', quiet_end: '07:00' })
  await api.answer('share', { shareable: [] })
  await api.answer('rules')
}

// /app/me answers, but only after `ms` milliseconds (a slow phone connection).
async function slowMe(page: Page, ms: number): Promise<void> {
  await page.route('**/api/app/me', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, ms))
    await route.continue().catch(() => undefined)
  })
}

// Give a page that is about to bounce the chance to: resolves once the path is `path` (or after `ms`).
async function settlesOn(page: Page, path: string, ms = 5000): Promise<void> {
  await page.waitForURL((u) => u.pathname === path, { timeout: ms }).catch(() => undefined)
}

// ----------------------------------------------------------------------------------
test.describe('the marketplace and the profile', () => {
  test('signed out: Hire Ava on the profile → sign up → back on the hire page', async ({ page, api }) => {
    void api
    await page.goto('/agents/ava')
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page).toHaveURL(/\/signup\?next=%2Fagents%2Fava%2Fhire$/)
    await fillSignup(page, newPerson('Mei Lin'))
    await signupForm(page).submit.click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
    await expect(page.getByRole('heading', { name: 'One thing before you start, Mei' })).toBeVisible()
  })

  test('the hero orb names Ava for what she is: a personal assistant', async ({ page, api }) => {
    void api
    await page.goto('/')
    await expect(page.getByText('Personal assistant').first()).toBeVisible()
    await expect(page.getByText("Hi, I'm Ava. New here, keen to learn.")).toBeVisible()
    await page.screenshot({ path: `${QA}/hero-orb-label.png` })
    await expect(page.getByLabel(/^Ava, an AI personal assistant$/i)).toHaveCount(1, { timeout: 2000 })
  })
})

test.describe('signed in with an account that has not hired', () => {
  test.use({ ownerState: 'account' })

  test('logged out with an account: Hire → "Log in" keeps the way back to the hire page', async ({ page, owner }) => {
    await page.context().clearCookies()
    await page.goto('/agents/ava')
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page).toHaveURL(/\/signup\?next=/)
    await page.getByRole('link', { name: /log in/i }).last().click()
    await expect(page).toHaveURL(/\/login\?next=%2Fagents%2Fava%2Fhire$/)
    await fillLogin(page, owner.email, owner.password)
    await loginForm(page).submit.click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
  })

  test('when the agent list cannot be loaded, the hire page says so instead of a blank notice', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/agents', (r) => r.fulfill(ME_DOWN))
    await page.goto('/agents/ava/hire')
    await expect(page.getByRole('heading', { name: /One thing before you start/ })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Hire Ava' })).toBeDisabled()
    await page.screenshot({ path: `${QA}/hire-catalog-error-silent.png`, fullPage: true })
    test.fail(true, 'BUG: if /agents fails, the hire page shows an empty notice and a dead Hire button with no message or retry')
    await expect(page.getByText(/can't|couldn't|try again/i)).toBeVisible({ timeout: 3000 })
  })
})

// ----------------------------------------------------------------------------------
test.describe('hired, not yet introduced', () => {
  test.use({ ownerState: 'hired' })

  test('the profile, the marketplace and the hire page lead back to the introduction', async ({ page, owner }) => {
    void owner
    await page.goto('/agents/ava')
    await expect(page.getByText('Works for you', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Open Ava' }).click()
    await expect(page).toHaveURL(/\/meet$/)
    await page.goto('/')
    await page.getByRole('button', { name: 'Go to your Ava' }).click()
    await expect(page).toHaveURL(/\/meet$/)
    await page.goto('/agents/ava/hire')
    await expect(page).toHaveURL(/\/meet$/)
  })

  test('if Ava cannot be loaded, /meet says so instead of sending you to the marketplace', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/me', (r) => r.fulfill(ME_DOWN))
    await page.goto('/meet')
    await settlesOn(page, '/')
    if (new URL(page.url()).pathname === '/') {
      // The marketplace says "Ava works for you"; "Go to your Ava" goes to /meet, which sends you straight back.
      await page.getByRole('button', { name: 'Go to your Ava' }).click()
      await page.waitForURL(/\/meet$/).catch(() => undefined)
      await settlesOn(page, '/')
      await page.screenshot({ path: `${QA}/me-fails-bounce.png`, fullPage: true })
    }
    await expect(page).toHaveURL(/\/meet$/, { timeout: 1000 })
    await expect(page.getByText(/can't reach|couldn't|try again/i)).toBeVisible({ timeout: 3000 })
  })

  test('finishing the introduction on a slow connection: "Start working with Ava" lands in the dashboard', async ({ page, api, owner }) => {
    void owner
    await upToConnect(api)
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Connect')
    await slowMe(page, 4000)
    await meet.finish().click()
    await meet.startWorking().click()
    expect((await query<{ step: string }>('SELECT step FROM onboarding'))[0]?.step).toBe('done')
    // It does reach the dashboard first; give it a moment to bounce back.
    await expect(page).toHaveURL(/\/dashboard$/)
    await settlesOn(page, '/meet', 3500)
    await page.screenshot({ path: `${QA}/finish-slow-me-bounce.png`, fullPage: true })
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 1000 })
  })

  test('if saving the end of the call fails, the call closes or says so; it never hangs on "Call ended"', async ({ page, api, owner }) => {
    void owner
    test.slow() // four questions, each read out
    await quickVoice(page)
    await openIntroduction(page, api)
    await page.route('**/api/app/intro/call/finish', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Something went wrong"}' }))
    const dialog = await startCall(page)
    for (const [i, q] of CALL_QUESTIONS.entries()) await typeAnswer(dialog, q, `Answer number ${i + 1}.`)
    await expect(dialog.getByText('Call ended').first()).toBeVisible()
    const closedOrExplained = async () => {
      // .first(): the call screen staying open WITH a message is a fine answer, and matches both.
      await expect(callDialog(page).or(page.getByText(/couldn't|try again|went wrong/i).first()).first()).toBeVisible()
      return !(await callDialog(page).isVisible()) || (await page.getByText(/couldn't|try again|went wrong/i).count()) > 0
    }
    await expect.poll(closedOrExplained, { timeout: 4000 }).toBe(true).catch(() => undefined)
    await page.screenshot({ path: `${QA}/call-finish-error-stuck.png`, fullPage: true })
    expect(await closedOrExplained()).toBe(true)
  })

  test('after a failed finish, leaving the call keeps the answers given', async ({ page, api, owner }) => {
    void owner
    test.slow()
    await quickVoice(page)
    await openIntroduction(page, api)
    await page.route('**/api/app/intro/call/finish', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"x"}' }))
    const dialog = await startCall(page)
    for (const [i, q] of CALL_QUESTIONS.entries()) await typeAnswer(dialog, q, `Answer number ${i + 1}.`)
    await expect(dialog.getByText('Call ended').first()).toBeVisible()
    await dialog.getByRole('button', { name: 'Leave the call' }).click()
    await dialog.getByRole('group', { name: 'Confirm leaving the call' }).getByRole('button', { name: 'Leave', exact: true }).click()
    await expect(dialog).toBeHidden()
    const rows = await query<{ n: number }>("SELECT count(*)::int AS n FROM memory_item WHERE source = 'intro_call'")
    expect(rows[0].n).toBe(4)
  })
})

// ----------------------------------------------------------------------------------
test.describe('introduced', () => {
  test('the marketplace, the profile and the hire page all lead to the dashboard', async ({ page, owner }) => {
    void owner
    await page.goto('/')
    await expect(page.getByText('Works for you', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Go to your Ava' }).click()
    await expect(page).toHaveURL(/\/dashboard/)
    await page.goto('/agents/ava')
    await page.getByRole('button', { name: 'Open Ava' }).click()
    await expect(page).toHaveURL(/\/dashboard/)
    await page.goto('/agents/ava/hire')
    await expect(page).toHaveURL(/\/dashboard/)
  })

  test('if Ava cannot be loaded, the dashboard says so instead of sending you to the marketplace', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/me', (r) => r.fulfill(ME_DOWN))
    await page.goto('/dashboard')
    await settlesOn(page, '/')
    await page.screenshot({ path: `${QA}/me-fails-dashboard-bounce.png`, fullPage: true })
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 1000 })
    await expect(page.getByText(/can't reach|couldn't|try again/i)).toBeVisible({ timeout: 3000 })
  })
})
