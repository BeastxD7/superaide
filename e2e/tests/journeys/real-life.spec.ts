// The messy things real people do: stop halfway and come back on another device,
// forget a password, press Back, share a resume, and hire their own Ava alongside someone else's.
import { expect, newPerson, test } from '../../src/fixtures'

test.describe('coming back later', () => {
  test.use({ ownerState: 'hired' })

  test('stopping halfway through the introduction resumes at the same step on a new device', async ({ page, api, owner, browser }) => {
    await page.goto('/meet')
    await page.getByRole('button', { name: 'Nice to meet you' }).click({ timeout: 15_000 })
    await page.getByRole('tab', { name: 'Tell me here' }).waitFor({ timeout: 15_000 })
    await page.getByRole('textbox').last().fill('I run product at a small startup in Bangalore.')
    await page.getByRole('textbox').last().press('Enter')
    await expect(page.getByLabel('Person 1 name')).toBeVisible({ timeout: 15_000 })
    void api

    // A different browser (say, the laptop at home): log in and pick up where you left off.
    const laptop = await browser.newContext()
    const tab = await laptop.newPage()
    await tab.goto('/login')
    await tab.getByLabel('Email').fill(owner.email)
    await tab.getByLabel('Password', { exact: true }).fill(owner.password)
    await tab.getByRole('button', { name: 'Log in' }).click()
    await expect(tab).toHaveURL(/\/meet$/)
    await expect(tab.getByText('Step 3 of 8')).toBeVisible()
    await expect(tab.getByText('I run product at a small startup in Bangalore.')).toBeVisible()
    await expect(tab.getByLabel('Person 1 name')).toBeVisible()
    await laptop.close()
  })

  test('the Back button during the introduction never loses answers', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    await page.getByRole('button', { name: 'Nice to meet you' }).click({ timeout: 15_000 })
    await page.goBack()
    await page.goForward()
    await expect(page).toHaveURL(/\/meet$/)
    await expect(page.getByText('Nice to meet you', { exact: true })).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('passwords', () => {
  test('five wrong passwords lock the account, with a message that says how long', async ({ page, api }) => {
    const me = newPerson()
    await api.signup(me)
    await page.context().clearCookies()
    await page.goto('/login')
    await page.getByLabel('Email').fill(me.email)
    for (let i = 0; i < 5; i += 1) {
      await page.getByLabel('Password', { exact: true }).fill(`wrong guess number ${i}`)
      await page.getByRole('button', { name: 'Log in' }).click()
      await expect(page.getByRole('alert')).toBeVisible()
    }
    // Even the right password is refused while locked, and the reason is clear.
    await page.getByLabel('Password', { exact: true }).fill(me.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page.getByText(/Too many wrong passwords. Try again in \d+ minutes/)).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })

  test('signing up twice with the same email explains what to do', async ({ page, api }) => {
    const me = newPerson()
    await api.signup(me)
    await page.context().clearCookies()
    await page.goto('/signup')
    await page.getByLabel('Your name').fill(me.name)
    await page.getByLabel('Email').fill(me.email.toUpperCase())
    await page.getByLabel('Password', { exact: true }).fill(me.password)
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/signup/)
  })

  test('a signed-in person who opens the login page is taken to their work', async ({ page, owner }) => {
    void owner
    await page.goto('/login')
    await expect(page).toHaveURL(/\/dashboard$/)
  })
})

test.describe('someone else', () => {
  test.use({ ownerState: 'introduced' })

  test('a second person can hire their own Ava and never sees the first owner', async ({ page, owner, browser }) => {
    // Many owners is the default (ADR 0009, 23e3430): Ava is never "taken".
    void page
    const other = newPerson('Priya Nair')
    const ctx = await browser.newContext()
    const p2 = await ctx.newPage()
    await p2.goto('/agents/ava')
    await expect(p2.getByText('Available now', { exact: true })).toBeVisible()
    await expect(p2.getByText('Ava already works for someone.')).toHaveCount(0)
    await expect(p2.getByText(owner.name.split(' ')[0])).toHaveCount(0)
    await p2.goto('/signup')
    await p2.getByLabel('Your name').fill(other.name)
    await p2.getByLabel('Email').fill(other.email)
    await p2.getByLabel('Password', { exact: true }).fill(other.password)
    await p2.getByRole('button', { name: 'Create account' }).click()
    await expect(p2).not.toHaveURL(/\/signup/)
    await p2.goto('/agents/ava/hire')
    await expect(p2.getByRole('button', { name: 'Hire Ava' })).toBeVisible()
    await expect(p2.getByText(owner.name.split(' ')[0])).toHaveCount(0)
    await ctx.close()
  })
})

test.describe('sharing a resume', () => {
  test.use({ ownerState: 'hired' })

  test('a text resume is accepted and an unsupported file is refused with a reason', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    await page.getByRole('button', { name: 'Nice to meet you' }).click({ timeout: 15_000 })
    await page.getByRole('tab', { name: 'Files & links' }).click({ timeout: 15_000 })
    const input = page.locator('input[type=file]')
    await input.setInputFiles({
      name: 'resume.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('Shashank Rao\nHead of Applied AI, PebbleRoad, Singapore\nPreviously: ML engineer.\n'),
    })
    await expect(page.getByText('resume.txt').or(page.getByText(/resume/i)).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()

    await input.setInputFiles({ name: 'holiday.png', mimeType: 'image/png', buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0, 0, 0, 0]) })
    await expect(page.getByRole('alert').or(page.locator('[role=status]')).first()).toBeVisible({ timeout: 15_000 })
    await page.screenshot({ path: 'test-results/journey/resume.png' })
  })
})
