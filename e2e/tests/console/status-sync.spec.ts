// #283 #288 #290: the header, sidebar, Home and Chat read one shared state for "what is Ava doing", so a change made outside the
// page (the operator pausing her) or on another page (the AI model) shows everywhere without a reload.
import { expect, test } from '../../src/fixtures'
import { Admin, composer, openChat } from '../../src/helpers/workspace'

const chip = (page: import('@playwright/test').Page) => page.locator('header').getByText(/^Ava: /)

test.describe('paused or resumed while the app is open', () => {
  test.skip(process.env.E2E_BRAIN === '1', 'this stack has an AI model on already')
  test.afterEach(async ({ page }) => {
    await new Admin(page.request).pause(false)
  })

  test('the header and Home follow a pause and a resume made in the console (focus and the light poll)', async ({ page, owner }, info) => {
    void owner
    const admin = new Admin(page.request)
    await page.clock.install()
    await page.goto('/dashboard')
    await expect(chip(page)).toHaveText('Ava: offline mode')
    await admin.pause(true)
    await page.clock.fastForward(16_000) // the light poll
    await expect(chip(page)).toHaveText('Ava: paused')
    // The wording differs by who paused her (the owner's own pause or the server operator's), the fact is the same.
    await expect(page.getByText(/is paused.*so nobody gets a reply/)).toBeVisible()
    await expect(page.getByText('Offline mode, infant stage')).toHaveCount(0)
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-paused-home-${info.project.name}.png` })
    await admin.pause(false)
    await page.evaluate(() => window.dispatchEvent(new Event('focus'))) // coming back to the tab
    await expect(chip(page)).toHaveText('Ava: offline mode')
    await expect(page.getByText(/is paused.*so nobody gets a reply/)).toHaveCount(0)
  })

  test('when Chat learns Ava is paused, the header says so too', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(chip(page)).toHaveText('Ava: offline mode')
    await new Admin(page.request).pause(true)
    await composer(page).fill('Are you there?')
    await composer(page).press('Enter')
    await expect(page.getByText(/is paused, so she didn't reply/)).toBeVisible()
    await expect(chip(page)).toHaveText('Ava: paused')
  })
})

test.describe('the AI model changes', () => {
  test.skip(process.env.E2E_BRAIN !== '1', 'needs E2E_BRAIN=1 (the fake Anthropic and Superaide AI credits)')

  test.use({ ownerState: 'hired' })

  test('the header, sidebar, Home and Chat follow "Use credits" and "Stop using credits"', async ({ page, api, owner }, info) => {
    void owner
    await api.answer('brain', { choice: 'skip' })
    await api.completeIntroduction()
    await page.goto('/dashboard/models')
    await expect(chip(page)).toHaveText('Ava: offline mode')
    await page.getByRole('button', { name: 'Use Superaide AI credits' }).click()
    await expect(page.getByText(/is thinking with/)).toBeVisible()
    await expect(chip(page)).toHaveText('Ava: online')
    await expect(page.getByText('Online, infant stage')).toBeVisible()
    await page.getByRole('link', { name: 'Home', exact: true }).click()
    await expect(page.getByText(/is on duty/)).toBeVisible()
    await expect(page.getByText(/in offline mode/)).toHaveCount(0)
    await page.getByRole('link', { name: 'Chat', exact: true }).click()
    await expect(page.getByText(/AI model isn't connected yet/)).toHaveCount(0)
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-model-chat-${info.project.name}.png` })
    await page.getByRole('link', { name: 'AI model', exact: true }).click()
    await page.getByRole('button', { name: 'Stop using credits' }).click()
    await expect(page.getByText(/No AI model is connected yet/)).toBeVisible()
    await expect(chip(page)).toHaveText('Ava: offline mode')
    await page.getByRole('link', { name: 'Home', exact: true }).click()
    await expect(page.getByText(/in offline mode/)).toBeVisible()
  })
})
