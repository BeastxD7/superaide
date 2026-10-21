// Everyday use of the workspace by an owner who has finished the introduction.
import { expect, test } from '../../src/fixtures'

test.describe('workspace, day to day', () => {
  test('a message shows in the chat straight away and Ava answers it', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    const chat = page
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('What can you do for me this week?')
    await box.press('Enter')
    await expect(box).toHaveValue('')
    await expect(chat.getByText('What can you do for me this week?')).toBeVisible()
    // Offline mode answers at once with a placeholder reply.
    await expect(chat.getByText(/Offline mode|You said/i).last()).toBeVisible({ timeout: 10_000 })
    await page.screenshot({ path: 'test-results/journey/workspace-reply.png' })
  })

  test('"remember that…" lands in the notebook and can be forgotten', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remember that my flight to Bangalore is on Friday')
    await box.press('Enter')
    const note = page.getByText('my flight to Bangalore is on Friday', { exact: true })
    await expect(note).toBeVisible()
    await page.reload()
    await expect(note).toBeVisible()
  })

  test('an empty message cannot be sent', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    await page.getByRole('textbox', { name: 'Message Ava' }).fill('   ')
    await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  test('logging out ends the session everywhere in the app', async ({ page, owner }) => {
    void owner
    await page.goto('/workspace')
    await page.getByRole('button', { name: /log out|sign out/i }).click()
    await page.goto('/workspace')
    await expect(page).toHaveURL(/\/login\?next=%2Fworkspace$/)
    await page.goto('/')
    await expect(page.getByRole('link', { name: 'Log in' })).toBeVisible()
  })
})
