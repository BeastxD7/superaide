// A long conversation is not silently cut at the newest page: the chat says earlier messages exist and loads
// them on request, introduction included (#299).
import { expect, test } from '../../src/fixtures'

test('the chat offers earlier messages when the conversation is longer than one page', async ({ page, api, owner }) => {
  void owner
  test.setTimeout(180_000)
  for (let i = 1; i <= 105; i++) await api.ok(api.post('/app/chat', { text: `history probe ${i}` }))

  await page.goto('/dashboard/chat')
  await expect(page.getByText('history probe 105', { exact: true })).toBeVisible()
  await expect(page.getByText('Showing the latest messages. Earlier ones are saved.')).toBeVisible()
  await expect(page.getByText('history probe 1', { exact: true })).toHaveCount(0) // cut off, and now it says so

  await page.getByRole('button', { name: 'Show earlier messages' }).click()
  await expect(page.getByText('history probe 1', { exact: true })).toBeAttached()
  await expect(page.getByText(/your personal assistant/).first()).toBeAttached() // the introduction is back
  await expect(page.getByRole('button', { name: 'Show earlier messages' })).toHaveCount(0) // reached the start

  // Sending another message does not make the older ones vanish.
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  await box.fill('one more')
  await box.press('Enter')
  await expect(page.getByText('one more', { exact: true })).toBeVisible()
  await expect(page.getByText('history probe 1', { exact: true })).toBeAttached()
  await expect(page.getByText('history probe 105', { exact: true })).toBeAttached()
})
