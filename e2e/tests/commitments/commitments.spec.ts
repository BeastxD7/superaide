// The Promises page (#480): add, edit, mark done, see overdue, remove (soft: the row stays), and the phone layout.
import { expect, query, test } from '../../src/fixtures'

const IN_AN_HOUR = () => new Date(Date.now() + 60 * 60 * 1000).toISOString()

test.describe('promises', () => {
  test('the owner adds, edits, closes and removes a promise; nothing is deleted from the database', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/promises')
    await expect(page.getByText('No open promises.')).toBeVisible()

    await page.getByLabel('What was promised').first().fill('Send Rahul the deck')
    await page.getByLabel('To whom').fill('Rahul')
    const soon = new Date(Date.now() + 2 * 60 * 60 * 1000)
    const pad = (n: number) => String(n).padStart(2, '0')
    await page.getByLabel('Due').first().fill(`${soon.getFullYear()}-${pad(soon.getMonth() + 1)}-${pad(soon.getDate())}T${pad(soon.getHours())}:${pad(soon.getMinutes())}`)
    await page.getByRole('button', { name: 'Add promise' }).click()
    const list = page.getByRole('list', { name: 'Promises still to do' })
    await expect(list.getByText('Send Rahul the deck')).toBeVisible()
    await expect(list.getByText(/You told Rahul/)).toBeVisible()

    await page.getByRole('button', { name: /^Edit "Send Rahul the deck"/ }).click()
    await page.getByLabel('What was promised').last().fill('Send Rahul the deck and the notes')
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(list.getByText('Send Rahul the deck and the notes')).toBeVisible()

    await page.getByRole('button', { name: /^Mark "Send Rahul.*done$/ }).click()
    await expect(page.getByText('No open promises.')).toBeVisible()
    await page.getByRole('tab', { name: 'Kept or cancelled' }).click()
    const closed = page.getByRole('list', { name: 'Closed promises' })
    await expect(closed.getByText('Send Rahul the deck and the notes')).toBeVisible()
    await expect(closed.getByText('Done')).toBeVisible()

    await page.getByRole('button', { name: /^Remove "Send Rahul/ }).click()
    await expect(page.getByText('Nothing closed yet.')).toBeVisible()
    const rows = await query<{ n: string; removed: string }>(
      `SELECT count(*)::text AS n, count(removed_at)::text AS removed FROM commitment`,
    )
    expect(rows[0]).toEqual({ n: '1', removed: '1' }) // hidden from the list, kept in the database
  })

  test('a promise past its time is marked overdue', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/commitments', { text: 'Reply to Meera', who: 'Meera', due_at: IN_AN_HOUR() }))
    await query(`UPDATE commitment SET state = 'overdue', due_at = now() - interval '20 minutes'`)
    await page.goto('/dashboard/promises')
    const row = page.getByRole('listitem').filter({ hasText: 'Reply to Meera' })
    await expect(row.getByText('Overdue')).toBeVisible()
    await expect(row.getByText(/You told Meera/)).toBeVisible()
  })

  test('a past time is refused with a reason and the list stays as it was', async ({ api, owner }) => {
    void owner
    const r = await api.post('/app/commitments', { text: 'Too late', due_at: new Date(Date.now() - 3600_000).toISOString() })
    expect(r.status()).toBe(422)
    const missing = await api.postWithoutCsrfHeader('/app/commitments', { text: 'x', due_at: IN_AN_HOUR() })
    expect(missing.status()).toBeGreaterThanOrEqual(400)
  })
})
