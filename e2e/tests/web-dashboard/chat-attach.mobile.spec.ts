// #511 at 390px: the attach button and the file chip fit next to the composer, with no sideways scroll.
import { expect, test } from '../../src/fixtures'
import { composer, expectNoSidewaysScroll, openChat } from '../../src/helpers/workspace'

test.use({ viewport: { width: 390, height: 780 } })

test('the attach button and a long file name fit 390px', async ({ page, owner }) => {
  void owner
  await openChat(page)
  const attachButton = page.getByRole('button', { name: 'Attach a PDF or text file' })
  await expect(attachButton).toBeInViewport()
  await page.getByTestId('chat-file-input').setInputFiles({
    name: 'a-very-long-file-name-for-the-quarterly-planning-review-final-v7.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('hello'),
  })
  await expect(page.getByTestId('chat-attachment')).toBeInViewport()
  await expect(composer(page)).toBeInViewport()
  await expect(page.getByRole('button', { name: 'Send' })).toBeInViewport()
  await expectNoSidewaysScroll(page)
})
