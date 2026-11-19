// "Try as a visitor" (#499): the owner chats with their own receptionist as a stranger would see it, inside a sandbox.
// Nothing is stored as a visitor's message, and replaying the same lines after a settings change shows the difference.
import { expect, test } from '../../src/fixtures'

const shot = (name: string) => (process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/${name}.png` : `test-results/${name}.png`)

test.describe('try as a visitor', () => {
  test('answers as a stranger would be answered, shows what it would do, and stores nothing', async ({ page, owner, api }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard/settings#try-as-visitor')
    const card = page.getByTestId('try-as-visitor')
    await expect(card.getByTestId('preview-banner')).toContainText('Nothing is sent')

    await card.getByLabel('What the visitor writes').fill('Hi, it is Meera. Please ask him to call me back by 5pm.')
    await card.getByRole('button', { name: 'Send' }).click()
    await expect(card.getByTestId('preview-chat')).toContainText('AI assistant')
    await expect(card.getByTestId('preview-stored')).toContainText('A message')
    await expect(card.getByTestId('preview-notifications')).toContainText('Message taken from')
    await expect(card.getByTestId('preview-spend')).toContainText('US$')
    await page.setViewportSize({ width: 1280, height: 1100 })
    await card.evaluate((el) => el.scrollIntoView()) // the page scrolls inside the window
    await page.screenshot({ path: shot('after-preview-1280') })

    // Nothing became a real visitor's message.
    const body = await api.get('/app/receptionist/messages').then((r) => r.json())
    expect(body.messages).toHaveLength(0)

    await page.setViewportSize({ width: 360, height: 1700 })
    await card.evaluate((el) => el.scrollIntoView())
    await expect(card.getByTestId('preview-panel')).toBeVisible()
    await page.screenshot({ path: shot('after-preview-360') })
  })

  test('replaying a script after changing a setting shows which answers changed', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard/settings#try-as-visitor')
    const card = page.getByTestId('try-as-visitor')
    await card.getByRole('button', { name: 'Replay Meera’s first visit' }).click()
    await expect(card.getByTestId('preview-chat')).toContainText('AI assistant')

    await page.getByLabel(/Use my first name/).click()
    await expect(page.getByText('Saved. This is how she introduces herself now.')).toBeVisible()
    await card.getByRole('button', { name: 'Replay Meera’s first visit' }).click()
    const compare = card.getByTestId('preview-compare')
    await expect(compare).toBeVisible()
    await expect(compare.locator('tr[data-changed="true"]').first()).toContainText('Ananya')
    await expect(compare.locator('tr[data-changed="true"]').first()).toContainText('Changed.')
    await page.setViewportSize({ width: 1280, height: 1500 })
    await card.evaluate((el) => el.scrollIntoView())
    await page.screenshot({ path: shot('after-replay-1280') })
  })

  test('only the owner can run a preview', async ({ api, browser }) => {
    void api
    const anonymous = await browser.newContext()
    const r = await anonymous.request.post('/api/app/receptionist/preview', {
      data: { messages: ['hi'] },
      headers: { 'X-Requested-With': 'superaide-e2e' },
    })
    expect([401, 403]).toContain(r.status())
    await anonymous.close()
  })
})
