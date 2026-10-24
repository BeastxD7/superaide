// Reminders on screen match what is real: Home does not list a reminder that already went off as "coming up"
// (#295), and a reminder another tab already cancelled leaves the list once the error is shown (#297).
import { expect, query, test } from '../../src/fixtures'

test.describe('reminders stay in step', () => {
  test('Home does not list a reminder that already went off as coming up, and counts the rest', async ({ page, api, owner }) => {
    void owner
    for (const t of ['Call Bob', 'Pay rent', 'Water plants', 'Book flights', 'Renew passport']) {
      await api.ok(api.post('/app/reminders', { text: `${t} in 3 days` }))
    }
    // Call Bob went off an hour ago: the delivery job marks it delivered and leaves it open.
    await query(
      `UPDATE intention SET due_at = now() - interval '2 hours', delivered_at = now() - interval '1 hour', delivered_via = '{app}'
       WHERE text LIKE 'Call Bob%'`,
    )
    await page.goto('/dashboard')
    const card = page.locator('[data-slot="card"]').filter({ has: page.getByText('Coming up', { exact: true }) })
    await expect(card.getByText('Pay rent')).toBeVisible()
    await expect(card.getByText('Call Bob')).toHaveCount(0)
    await expect(card.getByText('+1 more coming up')).toBeVisible()
    await expect(card.getByText('1 already sent, not marked done')).toBeVisible()

    // Reminders still shows the sent one, marked Sent.
    await page.goto('/dashboard/reminders')
    const bob = page.getByRole('listitem').filter({ hasText: 'Call Bob' })
    await expect(bob.getByText('Sent')).toBeVisible()
  })

  test('after "already cancelled" the reminder leaves the list', async ({ page, api, context, owner }) => {
    void owner
    await api.ok(api.post('/app/reminders', { text: 'Call the bank tomorrow at 10am' }))
    await page.goto('/dashboard/reminders')
    const other = await context.newPage()
    await other.goto('/dashboard/reminders')
    await expect(other.getByText('Call the bank')).toBeVisible()

    await page.getByRole('button', { name: /^Cancel "Call the bank/ }).click()
    await expect(page.getByText(/Nothing coming up/)).toBeVisible()

    await other.getByRole('button', { name: /^Mark "Call the bank.*done$/ }).click()
    await expect(other.getByText('This reminder is already cancelled.')).toBeVisible()
    await expect(other.getByText(/Nothing coming up/)).toBeVisible()
    await expect(other.getByRole('button', { name: /Mark "Call the bank/ })).toHaveCount(0)
  })
})
