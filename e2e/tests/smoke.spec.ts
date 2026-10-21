// Smoke tests: the stack is up and the most important path works end to end.
import { expect, newPerson, test } from '../src/fixtures'

test.describe('smoke', () => {
  test('a visitor sees Ava on the marketplace and opens her profile', async ({ page, api }) => {
    void api // fresh database
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.getByRole('link', { name: 'Meet Ava' }).first().click()
    await expect(page).toHaveURL(/\/agents\/ava$/)
    await expect(page).toHaveTitle(/Ava/)
  })

  test('sign up, hire Ava and arrive at the introduction (the session sticks)', async ({ page, api }) => {
    void api
    const person = newPerson()
    await page.goto('/agents/ava')
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page).toHaveURL(/\/signup\?next=/)
    await page.getByLabel('Your name').fill(person.name)
    await page.getByLabel('Email').fill(person.email)
    await page.getByLabel('Password', { exact: true }).fill(person.password)
    await page.getByRole('button', { name: 'Create account' }).click()
    await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
    await page.getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Hire Ava' }).click()
    await expect(page.getByRole('heading', { name: 'Ava is on your team' })).toBeVisible()
    // A reload keeps you signed in.
    await page.goto('/meet')
    await expect(page).toHaveURL(/\/meet$/)
  })

  test('a wrong password is refused with a clear message', async ({ page, api }) => {
    const person = newPerson()
    await api.signup(person)
    await page.context().clearCookies()
    await page.goto('/login')
    await page.getByLabel('Email').fill(person.email)
    await page.getByLabel('Password', { exact: true }).fill('not the password at all')
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })

  test('private pages send signed-out visitors to log in', async ({ page, api }) => {
    void api
    await page.goto('/workspace')
    await expect(page).toHaveURL(/\/login\?next=%2Fworkspace$/)
  })

  test('an introduced owner lands in the workspace', async ({ page, owner }) => {
    await page.goto('/workspace')
    await expect(page.getByRole('heading', { name: `Hello, ${owner.name.split(' ')[0]}. What shall we work on?` })).toBeVisible()
  })
})
