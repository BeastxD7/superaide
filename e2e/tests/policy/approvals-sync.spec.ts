// Approvals stay honest on screen: a request Ava is waiting on shows up while the app is open and is counted in
// the sidebar (#294), and one that ran out of time is not offered as something to say yes to (#291).
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

async function askFor(summary: string, opts: { expiredHoursAgo?: number } = {}) {
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status, expires_at)
     VALUES (gen_random_uuid(), $1, $2, now(), 'publish_post', $3::jsonb, 'pending',
             CASE WHEN $4::int IS NULL THEN NULL ELSE now() - make_interval(hours => $4::int) END)`,
    [TENANT, AGENT, JSON.stringify({ summary }), opts.expiredHoursAgo ?? null],
  )
}

test.describe('approvals stay in step with what Ava is waiting on', () => {
  test('a request made while Home is open appears on its own, with a count in the sidebar', async ({ page, owner }) => {
    void owner
    await page.clock.install()
    await page.goto('/dashboard')
    const link = page.locator('[data-sidebar="menu-button"][href="/dashboard/approvals"]')
    await expect(link).toBeVisible() // the sidebar lists Approvals
    await expect(page.getByText('Waiting for your OK')).toHaveCount(0)

    await askFor('Post: Launch day')
    await page.clock.fastForward(25_000)
    await expect(page.getByText('Waiting for your OK')).toBeVisible()
    await expect(page.getByText('Post: Launch day')).toBeVisible()
    await expect(link).toHaveText('Approvals1 waiting')

    // And the count follows the answer.
    await page.getByRole('button', { name: 'Yes, go ahead' }).click()
    await expect(link).toHaveText('Approvals')
  })

  test('a request that ran out of time is not offered, counted, or silently dropped', async ({ page, owner }) => {
    void owner
    await askFor('Post: We are hiring', { expiredHoursAgo: 2 })
    await askFor('Post: Launch day')
    await page.goto('/dashboard')

    await expect(page.getByText('Post: Launch day')).toBeVisible()
    await expect(page.getByText('Post: We are hiring')).toHaveCount(0) // not offered with a Yes
    await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toHaveCount(1)

    // The Approvals page keeps it in view, marked as expired and with nothing to press.
    await page.goto('/dashboard/approvals')
    const expired = page.getByRole('list', { name: 'Requests that ran out of time' })
    await expect(expired.getByText('Post: We are hiring')).toBeVisible()
    await expect(expired.getByText('Expired')).toBeVisible()
    await expect(expired.getByRole('button')).toHaveCount(0)

    // Neither the operator console's count nor the database treats it as waiting.
    const rows = await query<{ status: string }>("SELECT status FROM approval WHERE payload->>'summary' = 'Post: We are hiring'")
    expect(rows).toEqual([{ status: 'pending' }]) // untouched: nobody pressed Yes on it
  })
})
