// #511: the chat's attach button. A PDF or text file goes up with the message, Ava answers, and the file itself is
// never stored: only the caption (or a kind marker for a file alone) lands in the conversation. A wrong type or an
// oversize file is refused with a plain sentence.
import { expect, test } from '../../src/fixtures'
import { composer, expectNoSidewaysScroll, openChat, webConversation } from '../../src/helpers/workspace'

const SECRET = 'Lantern project budget is 4200 dollars'

async function attach(page: import('@playwright/test').Page, name: string, mimeType: string, buffer: Buffer) {
  await page.getByTestId('chat-file-input').setInputFiles({ name, mimeType, buffer })
}

test('attach a text file with a caption: it is read, answered, and never stored', async ({ page, owner }) => {
  void owner
  await openChat(page)
  await attach(page, 'notes.txt', 'text/plain', Buffer.from(SECRET))
  await expect(page.getByTestId('chat-attachment')).toContainText('notes.txt')
  await composer(page).fill('what does the file say?')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByTestId('chat-attachment')).toHaveCount(0)
  await expect.poll(async () => (await webConversation()).some((e) => e.content === 'what does the file say?')).toBe(true)
  await expect(page.getByText('what does the file say?', { exact: true }).last()).toBeVisible()
  const events = await webConversation()
  expect(events.some((e) => e.content.includes('Lantern'))).toBe(false)
  await expectNoSidewaysScroll(page)
})

test('a file alone can be sent; the log holds its kind, not its name or text', async ({ page, owner }) => {
  void owner
  await openChat(page)
  await attach(page, 'secret-name.txt', 'text/plain', Buffer.from(SECRET))
  await page.getByRole('button', { name: 'Send' }).click()
  await expect.poll(async () => (await webConversation()).some((e) => e.content === '[attachment read: text]')).toBe(true)
  const events = await webConversation()
  expect(events.some((e) => e.content.includes('secret-name') || e.content.includes('Lantern'))).toBe(false)
})

test('a program called .pdf is refused by its bytes, and the message is kept for another try', async ({ page, owner }) => {
  void owner
  await openChat(page)
  await attach(page, 'invoice.pdf', 'application/pdf', Buffer.concat([Buffer.from('MZ\x90\x00'), Buffer.alloc(64)]))
  await composer(page).fill('read this')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('I can only read PDFs and plain text files.')).toBeVisible()
  await expect(composer(page)).toHaveValue('read this') // the words are handed back
})

test('a file over 10 MB is refused before it uploads, and can be removed', async ({ page, owner }) => {
  void owner
  await openChat(page)
  await attach(page, 'big.txt', 'text/plain', Buffer.alloc(10 * 1024 * 1024 + 1, 97))
  await expect(page.getByText('That file is over 10 MB.')).toBeVisible()
  await expect(page.getByTestId('chat-attachment')).toHaveCount(0)
  await attach(page, 'ok.txt', 'text/plain', Buffer.from('fine'))
  await page.getByRole('button', { name: 'Remove the attached file' }).click()
  await expect(page.getByTestId('chat-attachment')).toHaveCount(0)
})
