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
    for (const private_ of ['Pune', 'Shashank', 'Rao']) expect(svg).not.toContain(private_)
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
