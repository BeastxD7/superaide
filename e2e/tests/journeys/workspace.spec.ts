// Everyday use of the workspace by an owner who has finished the introduction.
import { expect, test } from '../../src/fixtures'

test.describe('workspace, day to day', () => {
  test('a message shows in the chat straight away and Ava answers it @smoke', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    const chat = page
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('What can you do for me this week?')
    await box.press('Enter')
    await expect(box).toHaveValue('')
    await expect(chat.getByText('What can you do for me this week?', { exact: true })).toBeVisible()
    // Offline mode answers at once with a placeholder reply.
    await expect(chat.getByText(/Offline mode|You said/i).last()).toBeVisible({ timeout: 10_000 })
    await page.screenshot({ path: 'test-results/journey/workspace-reply.png' })
  })

  test('"remember that…" lands in the notebook and can be forgotten @smoke', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remember that my flight to Bangalore is on Friday')
    await box.press('Enter')
    await expect(page.getByText('Saved to your notebook. Only you can see it.')).toBeVisible()

    await page.getByRole('link', { name: 'Notebook', exact: true }).click()
    const note = page.getByText('my flight to Bangalore is on Friday', { exact: true })
    await expect(note).toBeVisible()
    await page.reload()
    await expect(note).toBeVisible()

    // Forgetting asks once, and then it's gone for good.
    const card = page.getByRole('listitem').filter({ has: note })
    await card.getByRole('button', { name: 'Forget this' }).click()
    await card.getByRole('button', { name: 'Keep' }).click()
    await expect(note).toBeVisible()
    await card.getByRole('button', { name: 'Forget this' }).click()
    await card.getByRole('button', { name: 'Forget', exact: true }).click()
    await expect(note).toHaveCount(0)
    await page.reload()
    await expect(note).toHaveCount(0)
  })

  test('typing before the history loads keeps your message and does not replay the history', async ({ page, owner }) => {
    void owner
    // A slow connection: the history takes a while to arrive.
    let held = true
    await page.route('**/api/app/messages', async (route) => {
      if (held) {
        held = false
        await new Promise((r) => setTimeout(r, 2500))
      }
      await route.continue()
    })
    await page.goto('/dashboard/chat')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('Quick one before you load')
    await box.press('Enter')
    // History arrives all at once, with your message still at the end and Ava's echo after it.
    await expect(page.getByText("That's us set up", { exact: false })).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('Quick one before you load', { exact: true })).toBeVisible()
    await expect(page.getByText(/You said: "Quick one before you load"/)).toBeVisible({ timeout: 5_000 })
  })

  test("Ava's Markdown is formatted, and HTML in a message is shown as text, never run", async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/chat')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    // The offline brain echoes what you say, so this reaches an agent bubble.
    await box.fill('**Skills and projects:** <img src=x onerror="window.__xss=1">')
    await box.press('Enter')
    const reply = page.getByText(/You said:/).last()
    await expect(reply).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('strong', { hasText: 'Skills and projects:' }).last()).toBeVisible()
    // Your own message stays exactly as typed; Ava's echo of it is formatted.
    await expect(reply).not.toContainText('**')
    expect(await page.locator('main img[src="x"], img[src="x"]').count()).toBe(0)
    expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined()
  })

  test('an empty message cannot be sent', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    await page.getByRole('textbox', { name: 'Message Ava' }).fill('   ')
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  test('logging out ends the session everywhere in the app', async ({ page, owner }) => {
    await page.goto('/dashboard')
    await page.getByRole('button', { name: new RegExp(owner.name) }).click()
    await expect(page.getByText('Signed in as')).toBeVisible()
    await page.getByRole('menuitem', { name: 'Log out' }).click()
    await expect(page).not.toHaveURL(/\/dashboard/)
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/)
    await page.goto('/')
    await expect(page.getByRole('link', { name: 'Log in' })).toBeVisible()
  })
})
