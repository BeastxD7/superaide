// Reminders tell the owner what is due (#345): a count pill on the sidebar item that appears and clears by itself,
// the Reminders page that updates without a reload, and a confirmation toast when Ava sets one in chat.
import AxeBuilder from '@axe-core/playwright'
import { expect, query, test } from '../src/fixtures'

const link = (page: import('@playwright/test').Page) => page.locator('[data-sidebar="menu-button"][href="/dashboard/reminders"]')

test.describe('reminders pill, list and toast', () => {
  test('a reminder that is due appears in the pill and the list by itself, and completing it clears the pill', async ({ page, api, owner }) => {
    void owner
    await page.clock.install()
    await page.goto('/dashboard')
    await expect(link(page)).toBeVisible()
    await expect(link(page)).toHaveText('Reminders') // nothing due: no pill

    await api.ok(api.post('/app/reminders', { text: 'Call Priya in 3 days' }))
    await api.ok(api.post('/app/reminders', { text: 'Pay rent in 3 days' }))
    await page.clock.fastForward(25_000)
    await expect(link(page)).toHaveText('Reminders') // later days are not counted

    await query(`UPDATE intention SET due_at = now() - interval '1 hour' WHERE text LIKE 'Call Priya%'`)
    await page.clock.fastForward(25_000)
    await expect(link(page)).toHaveText('Reminders1 due today')

    // The list gets it without a reload, and finishing it drops the pill at once.
    await link(page).click()
    const list = page.getByRole('list', { name: 'Upcoming reminders' })
    await expect(list.getByText('Call Priya')).toBeVisible()
    await page.getByRole('button', { name: /^Mark "Call Priya.*done$/ }).click()
    await expect(list.getByText('Call Priya')).toHaveCount(0)
    await expect(link(page)).toHaveText('Reminders')
    await expect(list.getByText('Pay rent')).toBeVisible()
  })

  test('done and cancelled reminders never count', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/reminders', { text: 'Old thing in 3 days' }))
    await query(`UPDATE intention SET due_at = now() - interval '1 day', status = 'done'`)
    await page.goto('/dashboard')
    await expect(link(page)).toBeVisible()
    await expect(link(page)).toHaveText('Reminders')
  })

  test('asking Ava in chat shows a toast with Open, and the page lists it', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/chat')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remind me to call Priya tomorrow at 5pm')
    await box.press('Enter')
    const toast = page.getByTestId('toast')
    await expect(toast).toContainText('Reminder set: Call Priya')
    await expect(page.getByRole('region', { name: 'Notifications' }).getByRole('status')).toContainText('Reminder set')
    const axe = await new AxeBuilder({ page }).include('[aria-label="Notifications"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
    expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' ')}`)).toEqual([])
    await toast.getByRole('link', { name: 'Open' }).click()
    await expect(page).toHaveURL(/\/dashboard\/reminders$/)
    await expect(page.getByRole('list', { name: 'Upcoming reminders' }).getByText('Call Priya')).toBeVisible()
    await expect(toast).toHaveCount(0) // Open dismisses it
  })

  test('a reminder that comes due while the app is open shows a toast with Snooze and Done', async ({ page, api, owner }) => {
    void owner
    await page.goto('/dashboard/notebook')
    await api.ok(api.post('/app/reminders', { text: 'Stretch in 3 days' }))
    await query(`UPDATE intention SET due_at = now() + interval '4 seconds' WHERE text LIKE 'Stretch%'`)
    // Tell the open page something changed (what the chat does when Ava sets one) so it reads the list now.
    await page.evaluate(() => window.dispatchEvent(new Event('superaide:reminders-changed')))
    const toast = page.getByTestId('toast')
    await expect(toast).toContainText('Reminder: Stretch', { timeout: 20_000 }) // the next 5 second check after it is due
    await toast.getByRole('button', { name: 'Done' }).click()
    await expect(toast).toHaveCount(0)
    await expect(async () => {
      const rows = await query<{ status: string }>(`SELECT status FROM intention WHERE text LIKE 'Stretch%'`)
      expect(rows[0].status).toBe('done')
    }).toPass()
  })
})
