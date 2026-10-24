// The Reminders count on a phone (390 wide): in the sidebar sheet, and on the tab bar when that build flag is on (#345).
import { expect, query, test } from '../src/fixtures'

test.use({ viewport: { width: 390, height: 844 } })

const FLAGS_ON = process.env.E2E_MOBILE_FLAGS === '1'

test.describe('reminders pill on a phone', () => {
  test('a due reminder is counted in the menu, the toast fits the screen, and nothing scrolls sideways', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/reminders', { text: 'Call Priya in 3 days' }))
    await query(`UPDATE intention SET due_at = now() - interval '1 hour'`)
    await page.goto('/dashboard/chat')

    if (FLAGS_ON) {
      const tab = page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /^Reminders/ })
      await expect(tab).toContainText('1')
      await expect(tab.getByText('due today')).toBeAttached()
    }
    await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
    const item = page.locator('[data-sidebar="menu-button"][href="/dashboard/reminders"]')
    await expect(item).toHaveText('Reminders1 due today')
    await item.click()
    await expect(page.getByRole('list', { name: 'Upcoming reminders' }).getByText('Call Priya')).toBeVisible()

    await page.goto('/dashboard/chat')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remind me to call Meera tomorrow at 5pm')
    await box.press('Enter')
    const toast = page.getByTestId('toast')
    await expect(toast).toContainText('Reminder set: Call Meera')
    const b = await toast.boundingBox()
    expect(b && b.x >= 0 && b.x + b.width <= 390).toBeTruthy()
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
    expect(sideways).toBe(false)
  })
})
