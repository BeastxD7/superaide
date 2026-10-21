// Reminders: ask in chat or add on the page; Ava brings them back on time.
import { expect, query, test } from '../../src/fixtures'

test.describe('reminders', () => {
  test('asking in chat creates one, it shows on the page, and it arrives on time', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/chat')
    const box = page.getByRole('textbox', { name: 'Message Ava' })
    await box.fill('remind me to call Priya tomorrow at 5pm')
    await box.press('Enter')
    // Between midnight and 4am "tomorrow" means the coming day, which is today's date (intentions/when.py).
    await expect(page.getByText(/Got it\. I'll remind you (today|tomorrow) at 5:00 pm: Call Priya/)).toBeVisible()

    await page.getByRole('link', { name: 'Reminders', exact: true }).click()
    await expect(page).toHaveURL(/\/dashboard\/reminders$/)
    const list = page.getByRole('list', { name: 'Upcoming reminders' })
    await expect(list.getByText('Call Priya')).toBeVisible()
    await expect(list.getByText(/(today|tomorrow) at 5:00 pm/)).toBeVisible()

    // Make it due now; the background worker delivers it into the chat.
    await query(`UPDATE intention SET due_at = now() - interval '1 second'`)
    await query(`UPDATE job SET run_at = now() - interval '1 second' WHERE kind = 'deliver_intention'`)
    await page.goto('/dashboard/chat')
    await expect(async () => {
      await page.reload()
      await expect(page.getByText('⏰ Reminder: Call Priya')).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 30_000 })
    const rows = await query<{ delivered_via: string[] }>('SELECT delivered_via FROM intention')
    expect(rows[0].delivered_via).toEqual(['app'])
  })

  test('add one on the page, snooze it, then mark it done', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/reminders')
    await expect(page.getByText(/Nothing coming up/)).toBeVisible()
    await page.getByLabel('New reminder').fill('Renew passport on 3 Dec')
    await page.getByRole('button', { name: 'Add reminder' }).click()
    const list = page.getByRole('list', { name: 'Upcoming reminders' })
    await expect(list.getByText('Renew passport')).toBeVisible()
    await expect(list.getByText(/Dec at 9:00 am/)).toBeVisible()

    await page.getByRole('button', { name: 'Snooze "Renew passport" for 1 hour' }).click()
    await expect(list.getByText(/today at|tomorrow at/)).toBeVisible()
    await page.getByRole('button', { name: 'Mark "Renew passport" done' }).click()
    await expect(page.getByText(/Nothing coming up/)).toBeVisible()
    expect((await query<{ status: string }>('SELECT status FROM intention'))[0].status).toBe('done')
  })

  test('a reminder without a time is refused with a hint', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/reminders')
    await page.getByLabel('New reminder').fill('Call mum')
    await page.getByRole('button', { name: 'Add reminder' }).click()
    await expect(page.getByText(/Add a time, like/)).toBeVisible()
  })
})
