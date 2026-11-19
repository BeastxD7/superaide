// The briefing on a phone (390 wide): the card and its reply box fit and nothing scrolls sideways.
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'
import { Admin } from '../../src/helpers/workspace'
import { away, takenMessage } from './seed'

test.use({ viewport: { width: 390, height: 844 } })

test('the briefing fits a phone', async ({ page, owner }) => {
  void owner
  const admin = new Admin(page.request, ADMIN_TOKEN)
  const r = await admin.post('/simulate', { provider: 'telegram', sender_id: '7001', sender_display: 'Meera Iyer', text: 'Hi, this is Meera.' })
  expect(r.ok()).toBeTruthy()
  await takenMessage('I need your answer on the very long project timeline question I raised on Friday afternoon, please.', {
    topic: 'the project timeline',
    rank: 'urgent',
  })
  await away()
  await page.goto('/dashboard')
  const card = page.getByTestId('briefing-card')
  await expect(card.getByRole('button', { name: 'Send the reply to Meera' })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})
