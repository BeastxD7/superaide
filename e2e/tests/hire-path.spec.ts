// A brand-new account finds the way to hire its own Ava without hunting (seen live: a friend
// reloaded the marketplace again and again and never found the hire page).
import { expect, newPerson, test } from '../src/fixtures'
import { fillSignup, signupForm } from '../src/helpers/accounts'
import { HirePage } from '../src/helpers/introduction'

test('sign up, see "Hire your own Ava" at once, hire, and start the introduction @smoke', async ({ page, api }) => {
  void api // a fresh database
  const person = newPerson('Priya Tan')
  await page.goto('/signup')
  await expect(signupForm(page).name).toBeFocused()
  await fillSignup(page, person)
  await signupForm(page).submit.click()

  await expect(page).toHaveURL('/')
  const hireYours = page.getByRole('button', { name: 'Hire your own Ava' })
  await expect(hireYours).toBeVisible()
  await hireYours.click()

  const hire = new HirePage(page)
  await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
  await expect(page.getByText('Ava is an AI personal assistant.')).toBeVisible()
  await hire.agree().check()
  await hire.hireButton().click()
  await expect(hire.hired()).toBeVisible()
  await hire.sayHello().click()
  await expect(page).toHaveURL(/\/meet$/)

  // Back on the marketplace, the button now leads to their own Ava.
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Go to your Ava' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Hire your own Ava' })).toHaveCount(0)
})

test('signed out, the marketplace offers no hire shortcut, only the agents', async ({ page, api }) => {
  void api
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Meet the agents' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Hire your own Ava' })).toHaveCount(0)
})
