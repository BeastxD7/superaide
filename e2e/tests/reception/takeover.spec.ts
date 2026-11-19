// Live takeover (#493): the owner writes to a visitor from Settings, Ava stays out of that conversation (no reply, no model
// call), the page says "You're handling this", and "Give control back to Ava" puts her back.
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'
import { Admin } from '../../src/helpers/workspace'

const shot = (name: string) => (process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/${name}.png` : `test-results/${name}.png`)

type Simulated = { mode: string; reply: string | null; notes: string[] }

async function visitorSays(admin: Admin, text: string): Promise<Simulated> {
  const r = await admin.post('/simulate', { provider: 'telegram', sender_id: '7001', sender_display: 'Meera Iyer', text })
  expect(r.ok(), `simulate → ${r.status()}`).toBeTruthy()
  return r.json()
}

test.describe('live takeover', () => {
  test('the owner steps in, Ava stays silent, and the button gives control back', async ({ page, owner }) => {
    void owner
    const admin = new Admin(page.request, ADMIN_TOKEN)
    await page.setViewportSize({ width: 1280, height: 800 })
    const first = await visitorSays(admin, 'Hi, this is Meera. Can you ask him to call me back?')
    expect(first.reply).toBeTruthy()

    await page.goto('/dashboard/settings#takeover')
    const row = page.getByTestId('takeover-row').first()
    await expect(row).toContainText('on Telegram')
    await expect(page.getByTestId('takeover-indicator')).toHaveCount(0)
    await row.getByLabel(/Your message to/).fill('Hi Meera, I will call you at noon.')
    await row.getByRole('button', { name: 'Send and take over' }).click()
    await expect(page.getByTestId('takeover-indicator')).toContainText('You’re handling this. Ava isn’t replying here.')
    await row.evaluate((el) => el.scrollIntoView())
    await page.screenshot({ path: shot('after-takeover-1280') })

    // Ava is out of it: the visitor's next message is stored, nobody answers it.
    const quiet = await visitorSays(admin, 'Thanks, are you still there?')
    expect(quiet.mode).toBe('takeover')
    expect(quiet.reply).toBeNull()
    await page.reload()
    await expect(page.getByTestId('takeover-messages')).toContainText('Thanks, are you still there?')
    await expect(page.getByTestId('takeover-messages')).toContainText('Hi Meera, I will call you at noon.')

    await page.setViewportSize({ width: 360, height: 740 })
    await row.evaluate((el) => el.scrollIntoView())
    await page.screenshot({ path: shot('after-takeover-360') })

    await row.getByRole('button', { name: 'Give control back to Ava' }).click()
    await expect(page.getByTestId('takeover-indicator')).toHaveCount(0)
    await expect(page.getByText('Ava is back in this conversation.')).toBeVisible()
    const back = await visitorSays(admin, 'Hello again?')
    expect(back.mode).toBe('stranger')
    expect(back.reply).toBeTruthy()
  })

  test('only the signed-in owner can step in or hand back', async ({ browser, page, owner }) => {
    void owner
    const admin = new Admin(page.request, ADMIN_TOKEN)
    await visitorSays(admin, 'Hi, this is Meera.')
    const anonymous = await browser.newContext()
    const id = '00000000-0000-0000-0000-000000000000'
    for (const path of [`/api/app/receptionist/conversations/${id}/write`, `/api/app/receptionist/conversations/${id}/release`]) {
      const r = await anonymous.request.post(path, { data: { text: 'hi' }, headers: { 'X-Requested-With': 'superaide-e2e' } })
      expect([401, 403]).toContain(r.status())
    }
    const list = await anonymous.request.get('/api/app/receptionist/conversations')
    expect([401, 403]).toContain(list.status())
    await anonymous.close()
  })
})
