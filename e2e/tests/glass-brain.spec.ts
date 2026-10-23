// The Glass brain (#55): a map of what Ava knows, where it came from, with correct and forget.
import { expect, test } from '../src/fixtures'

const dot = (page: import('@playwright/test').Page, text: string) =>
  page.getByRole('button', { name: `Note: ${text}`, exact: true })

test.describe('glass brain', () => {
  test.beforeEach(async ({ api, owner }) => {
    void owner
    await api.post('/app/chat', { text: 'Remember that I live in Pune' })
  })

  test('opens from the notebook and shows facts as dots with where they came from', async ({ page }) => {
    await page.goto('/dashboard/notebook')
    await page.getByRole('link', { name: /Glass brain/ }).click()
    await expect(page).toHaveURL(/\/dashboard\/notebook\/brain$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Glass brain' })).toBeVisible()
    const map = page.getByRole('group', { name: "Map of what Ava knows" })
    await expect(map).toBeVisible()
    await expect(page.getByRole('list', { name: 'Colour key' })).toContainText('You told Ava')
    await dot(page, 'I live in Pune').click()
    const details = page.getByRole('complementary', { name: 'Details' })
    await expect(details.getByText('I live in Pune')).toBeVisible()
    await expect(details.getByText('You told Ava')).toBeVisible()
    await expect(details.getByRole('button', { name: 'Correct this' })).toBeVisible()
  })

  test('correcting a fact replaces it and moves the old one to the forgotten shelf', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    await dot(page, 'I live in Pune').click()
    const details = page.getByRole('complementary', { name: 'Details' })
    await details.getByRole('button', { name: 'Correct this' }).click()
    await details.getByLabel('What is true').fill('I live in Singapore')
    await details.getByRole('button', { name: 'Save correction' }).click()
    await expect(dot(page, 'I live in Singapore')).toBeVisible()
    await expect(dot(page, 'I live in Pune')).toHaveCount(0)
    await expect(details.getByText('Pick a dot')).toBeVisible()
    await expect(page.getByText('I live in Pune')).toHaveCount(1) // only on the forgotten shelf
    await page.reload()
    await expect(dot(page, 'I live in Singapore')).toBeVisible()
  })

  test('a corrected fact stays on the map struck through, pointing at what replaced it', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    await dot(page, 'I live in Pune').click()
    const details = page.getByRole('complementary', { name: 'Details' })
    await details.getByRole('button', { name: 'Correct this' }).click()
    await details.getByLabel('What is true').fill('I live in Singapore')
    await details.getByRole('button', { name: 'Save correction' }).click()
    const old = page.getByRole('button', { name: 'Replaced note: I live in Pune', exact: true })
    await expect(old).toBeVisible()
    await expect(page.getByRole('list', { name: 'Colour key' })).toContainText('Struck through means replaced')
    await old.click()
    await expect(details.getByText('Replaced', { exact: true })).toBeVisible()
    await expect(details.locator('p.line-through', { hasText: 'I live in Pune' })).toBeVisible()
    await expect(details.locator('p', { hasText: 'I live in Singapore' })).toBeVisible()
    await expect(details.getByRole('button', { name: 'Correct this' })).toHaveCount(0) // history, not editable
    await details.getByRole('button', { name: 'Show the current note' }).click()
    await expect(details.locator('p', { hasText: 'I live in Singapore' })).toBeVisible()
    await expect(details.getByRole('button', { name: 'Correct this' })).toBeVisible()
  })

  test('the layout can be switched to free-floating and is remembered', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    const pos = async () => (await page.getByRole('button', { name: 'Note: I live in Pune', exact: true }).locator('circle').getAttribute('cx'))
    const tidy = await pos()
    await page.getByRole('button', { name: 'Free-floating' }).click()
    await expect(page.getByRole('button', { name: 'Free-floating' })).toHaveAttribute('aria-pressed', 'true')
    expect(await pos()).not.toBe(tidy)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Free-floating' })).toHaveAttribute('aria-pressed', 'true')
    await page.getByRole('button', { name: 'Tidy' }).click()
    expect(await pos()).toBe(tidy)
  })

  test('a fact can be locked and unlocked from the map', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    await dot(page, 'I live in Pune').click()
    const details = page.getByRole('complementary', { name: 'Details' })
    await details.getByRole('button', { name: 'Lock this' }).click()
    const locked = page.getByRole('button', { name: 'Locked note: I live in Pune', exact: true })
    await expect(locked).toBeVisible()
    await expect(details.getByText('Locked', { exact: true })).toBeVisible()
    await expect(details.getByText(/won.t merge, retire or fade this/)).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Locked note: I live in Pune', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Locked note: I live in Pune', exact: true }).click()
    await details.getByRole('button', { name: 'Unlock' }).click()
    await expect(dot(page, 'I live in Pune')).toBeVisible()
  })

  test('forgetting from the map removes the dot', async ({ page }) => {
    await page.goto('/dashboard/notebook/brain')
    await dot(page, 'I live in Pune').click()
    const details = page.getByRole('complementary', { name: 'Details' })
    await details.getByRole('button', { name: 'Forget this' }).click()
    await details.getByRole('button', { name: 'Forget', exact: true }).click()
    await expect(dot(page, 'I live in Pune')).toHaveCount(0)
  })

  test('the shareable picture has no names or notes, and works by keyboard', async ({ page, api }) => {
    const r = await api.get('/app/brain/share.svg')
    expect(r.status()).toBe(200)
    const svg = await r.text()
    expect(svg).toContain('<svg')
    for (const private_ of ['Pune', 'Ananya', 'Menon']) expect(svg).not.toContain(private_)
    await page.goto('/dashboard/notebook/brain')
    await dot(page, 'I live in Pune').focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('complementary', { name: 'Details' }).getByText('I live in Pune')).toBeVisible()
  })

  test('signed out, the map is refused', async ({ browser }) => {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    await page.goto('/dashboard/notebook/brain')
    await expect(page).toHaveURL(/\/login/)
    await ctx.close()
  })
})
