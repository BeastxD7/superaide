// QA (accounts) on a phone (Pixel 7): the account menu, changing your email, and a session that
// ends while the dashboard is open.
import type { Page } from '@playwright/test'
import { expect, query, test } from '../src/fixtures'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/accounts`

async function expectNoSideScroll(page: Page): Promise<void> {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }))
  expect(scrollWidth, 'page is wider than the screen').toBeLessThanOrEqual(innerWidth)
}

async function openSidebar(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Toggle Sidebar' }).first().tap()
}

test.describe('accounts on a phone', () => {
  test('Settings from the sidebar account menu closes the sidebar, as the page links do', async ({ page, owner }) => {
    test.fail(true, 'BUG: the account menu\'s Settings and Browse agents links never call setOpenMobile(false), so the sheet stays over the page')
    await page.goto('/dashboard')
    await openSidebar(page)
    await page.getByRole('button', { name: `Account: signed in as ${owner.name}` }).tap()
    const menu = page.getByRole('menu')
    await expect(menu).toContainText(owner.email)
    await menu.getByRole('menuitem', { name: 'Settings' }).tap()
    await expect(page).toHaveURL(/\/dashboard\/settings$/)
    await expect(page.locator('h1', { hasText: 'Settings' })).toBeAttached() // by CSS: the open sheet hides the page from the accessibility tree
    await page.screenshot({ path: `${QA}/phone-account-menu-settings-sheet-stays.png` })
    await expect(page.locator('[data-sidebar="sidebar"][data-mobile="true"]')).toBeHidden({ timeout: 2_000 })
    await expect(page.getByRole('form', { name: 'Change email' }).getByLabel('New email')).toBeInViewport()
  })

  test('a page link in the sidebar closes it (the behaviour the account menu should match)', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await openSidebar(page)
    await page.locator('[data-sidebar="menu-button"][href="/dashboard/settings"]').tap()
    await expect(page).toHaveURL(/\/dashboard\/settings$/)
    await expect(page.locator('[data-sidebar="sidebar"][data-mobile="true"]')).toBeHidden()
  })

  test('the email can be changed by touch, and the page fits the screen', async ({ page, owner }) => {
    await page.goto('/dashboard/settings')
    const form = page.getByRole('form', { name: 'Change email' })
    await form.getByLabel('New email').fill(`moved-${owner.email}`)
    await form.getByLabel('Password').fill(owner.password)
    await form.getByRole('button', { name: 'Change email' }).tap()
    await expect(form.getByText(`Your email is now moved-${owner.email}.`)).toBeVisible()
    await expectNoSideScroll(page)
    const box = await form.getByRole('button', { name: 'Change email' }).boundingBox()
    expect(box!.height, 'a finger-sized button').toBeGreaterThanOrEqual(32)
  })

  test('a long email in Settings fits the screen', async ({ page, owner, api }) => {
    const long = `a.very.long.address.for.testing.${'x'.repeat(40)}@example.com`
    await api.ok(api.post('/auth/email', { email: long, password: owner.password }))
    await page.goto('/dashboard/settings')
    await expect(page.getByText(`You sign in with ${long}.`)).toBeVisible()
    await expectNoSideScroll(page)
  })

  test('a session that ends while the dashboard is open sends you to log in on the next tap', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await expect(page.getByRole('button', { name: 'Toggle Sidebar' }).first()).toBeVisible()
    await page.waitForLoadState('networkidle') // see qa-accounts.spec.ts: let Home finish loading first
    await query('UPDATE user_session SET revoked_at = now()')
    await openSidebar(page)
    await page.locator('[data-sidebar="menu-button"][href="/dashboard/notebook"]').tap()
    await expect(page.getByText('Please log in.').first().or(page.getByRole('heading', { name: 'Welcome back' }))).toBeVisible()
    await page.screenshot({ path: `${QA}/session-ended-dashboard-phone.png`, fullPage: true })
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%2Fnotebook$/, { timeout: 3_000 })
  })
})
