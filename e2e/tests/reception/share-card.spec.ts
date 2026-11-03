// The share card on Settings (#475): four values, each with its own switch (off by default), the exact text Ava is
// told, her first words, the empty-card notice and the page the owner chat links to.
import { expect, test } from '../../src/fixtures'

test.describe('share card', () => {
  test('starts empty with every switch off; a value is told only once it is switched on', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    // The link the owner chat gives for "share my name".
    await page.goto('/dashboard/settings#share-card')
    const section = page.locator('#share-card')
    await expect(section).toBeVisible()
    await expect(section.getByText('Your card is empty')).toBeVisible()
    for (const label of ['Company', 'Role', 'City', 'Public email']) {
      await expect(section.getByRole('checkbox', { name: new RegExp(`${label}$`) })).not.toBeChecked()
    }
    await expect(section.getByTestId('share-card-preview')).toContainText('Nothing from the card.')
    await expect(section.getByTestId('share-card-preview')).toContainText('I can take a message.')
    await expect(section.getByText('How this works')).toBeVisible()
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-card-empty.png` : 'test-results/card-empty.png', fullPage: true })

    await section.locator('#card-company').fill('Larkfield')
    await section.locator('#card-role').fill('Designer')
    await section.getByRole('button', { name: 'Save the card' }).click()
    await expect(page.getByText('Saved. Switch a value on to let her say it.')).toBeVisible()
    // Saved, but nothing is switched on: still nothing told, still empty.
    await expect(section.getByTestId('share-card-preview')).toContainText('Nothing from the card.')
    await expect(section.getByText('Your card is empty')).toBeVisible()

    await section.getByRole('checkbox', { name: /Company$/ }).click()
    await expect(section.getByTestId('share-card-preview')).toContainText('Company: Larkfield')
    await expect(section.getByTestId('share-card-preview')).not.toContainText('Designer')
    await expect(section.getByTestId('share-card-preview')).toContainText('I can share what')
    await expect(section.getByText('Your card is empty')).toHaveCount(0)
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-card-on.png` : 'test-results/card-on.png', fullPage: true })

    await section.getByRole('checkbox', { name: /Company$/ }).click()
    await expect(section.getByTestId('share-card-preview')).toContainText('Nothing from the card.')
  })

  test('a bad email is refused with a plain message, and the card is the owner’s alone', async ({ page, owner, browser }) => {
    void owner
    await page.goto('/dashboard/settings#share-card')
    const section = page.locator('#share-card')
    await section.locator('#card-email').fill('jordan@larkfield')
    await section.getByRole('button', { name: 'Save the card' }).click()
    await expect(page.getByText(/look like an email/)).toBeVisible()
    const anonymous = await browser.newContext()
    expect((await anonymous.request.get('/api/app/receptionist')).status()).toBe(401)
    await anonymous.close()
  })
})
