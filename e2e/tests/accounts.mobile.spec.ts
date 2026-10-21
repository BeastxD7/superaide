// Accounts on a phone (Pixel 7): the sign-up and log-in forms are usable with
// touch, errors stay on screen, and nothing scrolls sideways.
import type { Locator, Page } from '@playwright/test'
import { expect, newPerson, test } from '../src/fixtures'
import { PASSWORD } from '../src/env'
import {
  MSG,
  expectFieldError,
  expectNoHorizontalScroll,
  fillLogin,
  fillSignup,
  loginForm,
  signupForm,
} from '../src/helpers/accounts'

async function gotoSignup(page: Page, next?: string): Promise<void> {
  await page.goto(`/signup${next ? `?next=${encodeURIComponent(next)}` : ''}`)
  await expect(signupForm(page).name).toBeFocused()
}

async function gotoLogin(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(loginForm(page).email).toBeFocused()
}

// A finger-sized target that is fully on screen.
async function expectTappable(page: Page, target: Locator): Promise<void> {
  await target.scrollIntoViewIfNeeded()
  await expect(target).toBeInViewport({ ratio: 1 })
  const box = await target.boundingBox()
  expect(box!.width).toBeGreaterThanOrEqual(24)
  expect(box!.height).toBeGreaterThanOrEqual(24)
}

test.describe('accounts on a phone', () => {
  test('sign-up: fits the screen, errors stay readable, and it completes by touch', async ({ page, api }) => {
    const taken = newPerson()
    await api.signup(taken)
    await page.context().clearCookies()

    await gotoSignup(page)
    await expectNoHorizontalScroll(page)
    const f = signupForm(page)

    // A long name and email still fit.
    await fillSignup(page, { name: 'Shashank '.repeat(13).trim(), email: taken.email, password: 'too short' })
    await expectNoHorizontalScroll(page)
    await expectTappable(page, f.submit)
    await f.submit.tap()
    await expectFieldError(page, f.password, MSG.shortPassword)
    await expectNoHorizontalScroll(page)

    await f.password.fill(PASSWORD)
    await f.submit.tap()
    await expectFieldError(page, f.email, MSG.duplicateEmail)
    await expect(page.getByRole('alert').filter({ hasText: MSG.duplicateEmail })).toBeInViewport()
    await expectNoHorizontalScroll(page)

    await f.email.fill(newPerson().email)
    await f.submit.tap()
    await expect(page).toHaveURL('/')
    await expectNoHorizontalScroll(page)
  })

  test('sign-up while hiring: the steps and the back link fit the screen', async ({ page, api }) => {
    void api
    await gotoSignup(page, '/agents/ava/hire')
    await expect(page.getByLabel('Hiring progress: step 1 of 3')).toBeInViewport()
    await expectTappable(page, page.getByRole('link', { name: 'Back' }))
    await expectNoHorizontalScroll(page)
    await fillSignup(page, newPerson())
    await signupForm(page).submit.tap()
    await expect(page).toHaveURL('/agents/ava/hire')
    await expectNoHorizontalScroll(page)
  })

  test('show password is a finger-sized button that works by touch', async ({ page, api }) => {
    void api
    await gotoLogin(page)
    const f = loginForm(page)
    await f.password.fill('secret words here')
    await expectTappable(page, f.showPassword)
    await f.showPassword.tap()
    await expect(f.password).toHaveAttribute('type', 'text')
    await page.getByRole('button', { name: 'Hide password' }).tap()
    await expect(f.password).toHaveAttribute('type', 'password')
  })

  test('log-in: a wrong password is shown on screen, then the right one works', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()

    await gotoLogin(page)
    await expectNoHorizontalScroll(page)
    const f = loginForm(page)
    await fillLogin(page, person.email, 'not the password at all')
    await expectTappable(page, f.submit)
    await f.submit.tap()
    await expect(page.getByText(MSG.badLogin)).toBeInViewport()
    await expectNoHorizontalScroll(page)

    await f.password.fill(person.password)
    await f.submit.tap()
    await expect(page).toHaveURL('/')
    await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible()
    await expectNoHorizontalScroll(page)
  })

  test('log-out works from the phone nav', async ({ page, api }) => {
    await api.signup(newPerson())
    await page.goto('/')
    await expectNoHorizontalScroll(page)
    const logout = page.getByRole('navigation').getByRole('button', { name: 'Log out' })
    await expectTappable(page, logout)
    await logout.tap()
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()
    expect((await api.get('/auth/me')).status()).toBe(401)
  })

  test('the 404 page fits the screen', async ({ page, api }) => {
    void api
    const response = await page.goto('/nope')
    expect(response?.status()).toBe(404)
    await expect(page.getByRole('heading', { name: "There's no page here" })).toBeInViewport()
    await expectTappable(page, page.getByRole('link', { name: 'See all agents' }))
    await expectNoHorizontalScroll(page)
  })
})
