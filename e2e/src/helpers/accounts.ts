// Helpers for the accounts-and-access specs (sign-up, log-in, sessions, redirects).
import type { BrowserContext, Cookie, Locator, Page } from '@playwright/test'
import { expect, type Person } from '../fixtures'

export const SESSION_COOKIE = 'superaide_session'

// The messages a person sees. Kept here so a wording change is one edit.
export const MSG = {
  shortPassword: 'Use at least 10 characters.',
  longPassword: 'Use at most 256 characters.',
  commonPassword: 'That password is too easy to guess. Try a short sentence.',
  personalPassword: "Don't use your name or email in the password.",
  invalidEmail: 'Enter a valid email address.',
  duplicateEmail: "There's already an account with this email. Log in instead.",
  badLogin: "That email and password don't match.",
  locked: /Too many wrong passwords\. Try again in \d+ minutes?\./,
  passwordHint: 'At least 10 characters. A short sentence works well.',
} as const

// Hostile `next=` values that must never take the visitor off the site.
export const HOSTILE_NEXT = ['//evil.example', 'https://evil.example', '/\\evil.example', 'javascript:alert(1)'] as const

export function signupForm(page: Page) {
  return {
    name: page.getByLabel('Your name'),
    email: page.getByLabel('Email'),
    password: page.getByLabel('Password', { exact: true }),
    showPassword: page.getByRole('button', { name: 'Show password' }),
    hidePassword: page.getByRole('button', { name: 'Hide password' }),
    submit: page.getByRole('button', { name: 'Create account' }),
  }
}

export function loginForm(page: Page) {
  return {
    email: page.getByLabel('Email'),
    password: page.getByLabel('Password', { exact: true }),
    showPassword: page.getByRole('button', { name: 'Show password' }),
    submit: page.getByRole('button', { name: 'Log in', exact: true }),
  }
}

export async function fillSignup(page: Page, p: Pick<Person, 'name' | 'email' | 'password'>): Promise<void> {
  const f = signupForm(page)
  await f.name.fill(p.name)
  await f.email.fill(p.email)
  await f.password.fill(p.password)
}

export async function fillLogin(page: Page, email: string, password: string): Promise<void> {
  const f = loginForm(page)
  await f.email.fill(email)
  await f.password.fill(password)
}

// The error for one field: shown as an alert directly under that field, the
// field is marked invalid, and no other field is.
export async function expectFieldError(page: Page, field: Locator, message: string): Promise<void> {
  const alert = page.getByRole('alert').filter({ hasText: message })
  await expect(alert).toBeVisible()
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  const invalid = page.locator('form [aria-invalid="true"]')
  await expect(invalid).toHaveCount(1)

  const f = await field.boundingBox()
  const a = await alert.boundingBox()
  expect(f, 'field is on screen').not.toBeNull()
  expect(a, 'error is on screen').not.toBeNull()
  // Just below the field (within a line or two), and lined up with it.
  expect(a!.y).toBeGreaterThanOrEqual(f!.y + f!.height - 1)
  expect(a!.y - (f!.y + f!.height)).toBeLessThan(40)
  expect(Math.abs(a!.x - f!.x)).toBeLessThan(24)
}

export async function sessionCookie(context: BrowserContext): Promise<Cookie | undefined> {
  return (await context.cookies()).find((c) => c.name === SESSION_COOKIE)
}

// A session cookie for the given host, as a browser would store it.
export function forgedCookie(value: string, url: string): Cookie {
  const { hostname } = new URL(url)
  return {
    name: SESSION_COOKIE,
    value,
    domain: hostname,
    path: '/',
    expires: -1,
    httpOnly: true,
    secure: false,
    sameSite: 'Lax',
  }
}

// The public site's account menu (in the nav): a button named after the signed-in
// account opens a small menu with "Log out".
export function accountButton(page: Page): Locator {
  return page.getByRole('navigation').getByRole('button', { name: /^Account: signed in as / })
}

export function navLogOut(page: Page): Locator {
  return page.getByRole('menuitem', { name: 'Log out' }) // the menu is portalled out of the nav
}

// Log out from the public site's nav (marketplace, agent pages, hire, introduction).
export async function logOutFromNav(page: Page): Promise<void> {
  await accountButton(page).click()
  await navLogOut(page).click()
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Log in' })).toBeVisible()
}

// The page fits the screen: nothing sticks out sideways.
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(scrollWidth, 'page is wider than the screen').toBeLessThanOrEqual(innerWidth)
}

// Record (and block) any attempt to reach another site, e.g. via a hostile next=.
export async function trapOffsiteRequests(context: BrowserContext, host = 'evil.example'): Promise<string[]> {
  const hits: string[] = []
  await context.route(
    (url) => url.hostname.endsWith(host),
    (route) => {
      hits.push(route.request().url())
      return route.abort()
    },
  )
  return hits
}

// Errors thrown in the page (a crash), collected for the whole test.
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  return errors
}
