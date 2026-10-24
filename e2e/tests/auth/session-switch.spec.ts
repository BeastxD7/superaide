// #293: a tab must not keep one person's name over another person's data after a second sign-in in the same browser.
// Two pages of one browser context share the one session cookie, like two tabs.
import { expect, newPerson, test } from '../../src/fixtures'
import { newVisitor } from '../../src/helpers/workspace'

const OTHER = newPerson('Other Person')

async function signUpAsOther(api: import('../../src/fixtures').Api) {
  await api.signup(OTHER)
  await api.hire()
  await api.completeIntroduction()
}

test('a tab notices that the browser now belongs to someone else (focus)', async ({ page, api, owner }, info) => {
  await page.goto('/dashboard/settings')
  await expect(page.locator('[data-sidebar="footer"]').getByText(owner.name)).toBeVisible()
  await signUpAsOther(api) // "the second tab": the one session cookie is now the other person's
  await page.evaluate(() => window.dispatchEvent(new Event('focus'))) // coming back to the first tab
  await expect(page.locator('[data-sidebar="footer"]').getByText('Other Person')).toBeVisible()
  await expect(page.locator('[data-sidebar="footer"]').getByText(owner.name)).toHaveCount(0)
  await page.getByRole('link', { name: 'Chat', exact: true }).click()
  await expect(page.getByText('Other', { exact: false }).first()).toBeVisible()
  await expect(page.locator('[data-sidebar="footer"]').getByText('Other Person')).toBeVisible() // name and data agree
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-switch-${info.project.name}.png` })
})

test('a sign-in in another tab updates this one at once, without focus (BroadcastChannel)', async ({ page, context, browser, api, owner }) => {
  await page.goto('/dashboard/settings')
  await expect(page.locator('[data-sidebar="footer"]').getByText(owner.name)).toBeVisible()
  // The other person already has an account (made in a separate browser so this one keeps owner A's cookie)...
  const other = await newVisitor(browser)
  await other.api.signup(OTHER)
  await other.api.hire()
  await other.api.completeIntroduction()
  // ...and signs in through the login page of a second tab of this browser: the second tab tells the first.
  await api.post('/auth/logout') // the second tab signs A out first, as a person would, so the login page opens
  const second = await context.newPage()
  await second.goto('/login')
  await second.getByLabel('Email').fill(OTHER.email)
  await second.locator('input[type="password"]').fill(OTHER.password)
  await second.getByRole('button', { name: 'Log in' }).click()
  await expect(second).toHaveURL(/\/dashboard/)
  await expect(page.locator('[data-sidebar="footer"]').getByText('Other Person')).toBeVisible()
  await other.context.close()
})
