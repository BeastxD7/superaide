// The Glass brain share link (#55): an unlisted, expiring, revocable link to the counts-only picture. Behind
// BRAIN_SHARE_ENABLED, so it runs only with E2E_BRAIN_SHARE=1
// (e.g. `E2E_BRAIN_SHARE=1 E2E_SLOT=49 npx playwright test brain-share`).
import { BRAIN_SHARE } from '../src/env'
import { expect, test } from '../src/fixtures'

test.skip(!BRAIN_SHARE, 'BRAIN_SHARE_ENABLED is off by default; set E2E_BRAIN_SHARE=1 to run these')

test.describe('glass brain share link', () => {
  test.beforeEach(async ({ api, owner }) => {
    void owner
    await api.post('/app/chat', { text: 'Remember that I live in Pune' })
    await api.post('/app/chat', { text: 'Remember that my secret project is called Bluebird' })
  })

  test('the owner creates a link, a visitor sees counts only, and turning it off stops it', async ({ page, browser }) => {
    await page.goto('/dashboard/notebook/brain')
    const panel = page.getByRole('region', { name: /Share the picture by a link/ })
    await panel.getByLabel('Lasts').selectOption('3')
    await panel.getByRole('button', { name: 'Create link' }).click()
    const url = await panel.getByLabel('Share link').inputValue()
    expect(url).toMatch(/\/s\/sa_brain_/)
    await expect(panel.getByText('shown only once')).toBeVisible()
    await expect(panel.getByRole('list', { name: 'Your share links' })).toContainText('active')

    const visitor = await browser.newContext()
    const v = await visitor.newPage()
    await v.goto(url)
    await expect(v.getByRole('heading', { level: 1, name: /shape of an assistant/ })).toBeVisible()
    await expect(v.getByText(/things, by topic/)).toBeVisible()
    await expect(v.getByRole('img', { name: /Shape of the memory/ })).toBeVisible()
    const body = await v.locator('body').innerText()
    for (const private_ of ['Pune', 'Bluebird', 'Rao']) expect(body).not.toContain(private_)
    expect(await v.locator('head meta[name=robots]').getAttribute('content')).toContain('noindex')

    await panel.getByRole('button', { name: 'Turn off' }).click()
    await expect(panel.getByRole('list', { name: 'Your share links' })).toContainText('revoked')
    await v.reload()
    await expect(v.getByRole('heading', { name: /no page here/i })).toBeVisible()
    await visitor.close()
  })

  test('an unknown or forged link is the ordinary not-found page', async ({ browser }) => {
    const visitor = await browser.newContext()
    const v = await visitor.newPage()
    await v.goto('/s/sa_brain_' + 'x'.repeat(43))
    await expect(v.getByRole('heading', { name: /no page here/i })).toBeVisible()
    await visitor.close()
  })

  test('a link cannot last longer than 7 days', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    await expect(page.getByLabel('Lasts')).toBeVisible()
    const options = await page.getByLabel('Lasts').locator('option').allTextContents()
    expect(options).toHaveLength(7)
    expect(options.at(-1)).toBe('7 days')
  })
})
