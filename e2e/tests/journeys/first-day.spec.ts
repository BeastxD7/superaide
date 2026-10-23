// A new owner's first day, through the UI only, the way a person would do it:
// browse, sign up, hire Ava, introduce yourself step by step, then use the workspace,
// leave, and come back. Screenshots of every step land in test-results/journey/.
import { expect, newPerson, test } from '../../src/fixtures'
import type { Page } from '@playwright/test'

let shot = 0
async function snap(page: Page, name: string) {
  shot += 1
  await page.screenshot({ path: `test-results/journey/${String(shot).padStart(2, '0')}-${name}.png`, fullPage: true })
}

// Ava answers after a short "typing" pause; wait for the next step's controls.
async function nextStep(page: Page, label: RegExp) {
  await expect(page.getByText(label).first()).toBeVisible({ timeout: 15_000 })
}

test('a new owner browses, hires Ava, introduces themselves and gets to work @smoke', async ({ page, api }) => {
  void api // fresh database
  test.setTimeout(120_000)
  const me = newPerson('Ananya Menon')

  await test.step('browse the marketplace and read about Ava', async () => {
    await page.goto('/')
    await snap(page, 'landing')
    await page.getByRole('link', { name: 'Meet Ava' }).first().click()
    await expect(page).toHaveURL(/\/agents\/ava$/)
    await snap(page, 'profile')
  })

  await test.step('sign up, making a typo in the password first', async () => {
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await page.getByLabel('Your name').fill(me.name)
    await page.getByLabel('Email').fill(me.email)
    await page.getByLabel('Password', { exact: true }).fill('short')
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page.getByText(/at least 10 characters/i)).toBeVisible()
    await page.getByLabel('Password', { exact: true }).fill(me.password)
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
    await snap(page, 'notice')
  })

  await test.step('read the notice, agree and hire', async () => {
    await expect(page.getByRole('button', { name: 'Hire Ava' })).toBeDisabled()
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page.getByRole('heading', { name: 'Ava is on your team' })).toBeVisible()
    await snap(page, 'hired')
    await page.getByRole('button', { name: /Say hello/ }).click()
    await expect(page).toHaveURL(/\/meet$/)
  })

  await test.step('say hello', async () => {
    await page.getByRole('button', { name: 'What will you do for me?' }).click({ timeout: 15_000 })
    await snap(page, 'hello')
  })

  await test.step('tell Ava about yourself in your own words', async () => {
    const box = page.getByRole('textbox').last()
    await expect(box).toBeVisible({ timeout: 15_000 })
    await snap(page, 'about')
    await box.fill('I lead applied AI at PebbleRoad in Singapore. Mornings are for deep work; I hate surprise meetings.')
    await box.press('Enter')
  })

  await test.step('finish introducing yourself', async () => {
    // After a chat answer Ava may ask for more or offer to move on.
    const done = page.getByRole('button', { name: /done introducing|That's me|Continue|Next/i }).first()
    if (await done.isVisible({ timeout: 10_000 }).catch(() => false)) await done.click()
    await snap(page, 'after-about')
  })

  await test.step('name the people who matter', async () => {
    await page.getByLabel('Person 1 name').fill('Priya', { timeout: 15_000 })
    await page.getByLabel('Person 2 name').fill('Arjun')
    await snap(page, 'people')
    await page.getByRole('button', { name: 'These are my people' }).click()
  })

  await test.step('say what counts as urgent', async () => {
    await nextStep(page, /digest/i)
    await page.getByLabel("Something else that's urgent").fill('Anything about the Series A')
    await snap(page, 'urgent')
    await page.getByRole('button', { name: "That's what's urgent" }).click()
  })

  await test.step('choose how and when to be reached', async () => {
    await nextStep(page, /Quiet hours/)
    await page.getByLabel('Quiet hours start').fill('22:00')
    await page.getByLabel('Quiet hours end').fill('07:00')
    await snap(page, 'reach')
    await page.getByRole('button', { name: 'Save how to reach me' }).click()
  })

  await test.step('decide what Ava may share', async () => {
    await page.getByLabel('Never share').fill('My home address and anything about my health', { timeout: 15_000 })
    await snap(page, 'share')
    await page.getByRole('button', { name: "That's what you can share" }).click()
  })

  await test.step('agree to the ground rules', async () => {
    await page.getByRole('button', { name: 'Sounds good' }).click({ timeout: 15_000 })
  })

  await test.step('skip connections for now and start working', async () => {
    await snap(page, 'connect')
    await page.getByRole('button', { name: 'Finish' }).click({ timeout: 15_000 })
    await page.getByRole('button', { name: /Start working with Ava/ }).click({ timeout: 15_000 })
    await expect(page).toHaveURL(/\/dashboard$/)
    await snap(page, 'dashboard')
    await page.getByRole('link', { name: 'Chat', exact: true }).click()
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
  })

  await test.step('ask Ava something in the chat', async () => {
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remember that my flight to Bangalore is on Friday')
    await box.press('Enter')
    await expect(page.getByText(/Bangalore/).first()).toBeVisible()
    await page.waitForTimeout(3000)
    await snap(page, 'chat')
    await page.getByRole('link', { name: 'Notebook', exact: true }).click()
    await expect(page.getByText('my flight to Bangalore is on Friday', { exact: true })).toBeVisible()
    await snap(page, 'notebook')
  })

  await test.step('leave and come back the next day', async () => {
    await page.reload()
    await expect(page).toHaveURL(/\/dashboard\/notebook$/)
    await page.goto('/')
    await snap(page, 'landing-signed-in')
    await page.goto('/agents/ava')
    await snap(page, 'profile-signed-in')
  })
})
