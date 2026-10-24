// #441: a capability has its own pages under /dashboard/capabilities/<capability>, with breadcrumbs and a sub-navigation,
// and the old addresses redirect permanently. Desktop here, the phone in capability-routes.mobile.spec.ts.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

async function installGmail(api: import('../../src/fixtures').Api) {
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const gmail = cards.find((c) => c.id === 'superaide/gmail')!
  await api.ok(api.post('/app/skills/superaide/gmail/install', { accepted: gmail.permissions.map((p) => p.id) }))
}

test('Gmail has an overview and a Reading page with breadcrumbs, a sub-navigation and no axe violations', async ({ page, api, owner }, info) => {
  void owner
  await installGmail(api)
  await page.goto('/dashboard/capabilities/gmail')
  await expect(page.getByRole('heading', { level: 1, name: 'Gmail' })).toBeVisible()
  const crumbs = page.getByRole('navigation', { name: 'breadcrumb' })
  await expect(crumbs.getByRole('link', { name: 'Capabilities' })).toBeVisible()
  const sub = page.getByRole('navigation', { name: 'Gmail settings' })
  await expect(sub.getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-gmail-overview-${info.project.name}.png` })

  await sub.getByRole('link', { name: 'Reading' }).click()
  await expect(page).toHaveURL(/\/dashboard\/capabilities\/gmail\/reading$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Reading' })).toBeVisible()
  await expect(crumbs.getByRole('link', { name: 'Gmail' })).toBeVisible()
  await expect(sub.getByRole('link', { name: 'Reading' })).toHaveAttribute('aria-current', 'page')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-gmail-reading-${info.project.name}.png` })
  await page.waitForTimeout(500)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  expect(results.violations.map((v) => v.id)).toEqual([])
})

test('the capabilities index links to a capability\'s settings, and an uninstalled one says so', async ({ page, api, owner }) => {
  void owner
  await page.goto('/dashboard/capabilities/gmail')
  await expect(page.getByText("Gmail isn't installed yet.")).toBeVisible()
  await installGmail(api)
  await page.goto('/dashboard/capabilities')
  await page.getByRole('link', { name: 'Gmail settings' }).click()
  await expect(page).toHaveURL(/\/dashboard\/capabilities\/gmail$/)
})

test('an unknown capability or setting shows the not-found page, not a blank one', async ({ page, owner }) => {
  void owner
  // (Inside the dashboard the page is streamed, so the status line is already sent; what matters to a person is the page.)
  for (const path of ['/dashboard/capabilities/nope', '/dashboard/capabilities/gmail/nope']) {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: "There's no page here" })).toBeVisible()
  }
})

test.describe('old addresses redirect', () => {
  test('?gmail=... (the sign-in return) goes to the Gmail page, permanently, keeping the result', async ({ page, owner }) => {
    void owner
    const res = await page.request.get('/dashboard/capabilities?gmail=api_disabled', { maxRedirects: 0 })
    expect(res.status()).toBe(308)
    expect(res.headers()['location']).toContain('/dashboard/capabilities/gmail?gmail=api_disabled')
  })
  test('#gmail and #gmail-read anchors land on the Gmail pages', async ({ page, api, owner }) => {
    void owner
    await installGmail(api)
    await page.goto('/dashboard/capabilities#gmail')
    await expect(page).toHaveURL(/\/dashboard\/capabilities\/gmail$/)
    await page.goto('/dashboard/capabilities#gmail-read')
    await expect(page).toHaveURL(/\/dashboard\/capabilities\/gmail\/reading$/)
  })
})

test('the old Principal card address redirects permanently to Gmail > Signature, and the signature page opens without Gmail installed', async ({ page, owner }) => {
  void owner
  const res = await page.request.get('/dashboard/principal-card', { maxRedirects: 0 })
  expect(res.status()).toBe(308)
  expect(res.headers()['location']).toContain('/dashboard/capabilities/gmail/signature')
  await page.goto('/dashboard/principal-card')
  await expect(page.getByRole('heading', { level: 1, name: 'Signature' })).toBeVisible()
  await expect(page.getByLabel('Your first name (optional)')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Principal card' })).toHaveCount(0) // no menu item
})
