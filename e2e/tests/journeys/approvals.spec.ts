// Ava asks before acting: a @smoke request waits on the dashboard until the owner says yes or no.
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

async function askFor(kind: string, summary: string) {
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), $3, $4::jsonb, 'pending')`,
    [TENANT, AGENT, kind, JSON.stringify({ summary })],
  )
}

test.describe('approvals', () => {
  test('a request waits on the dashboard, and "Yes" decides it once', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await expect(page.getByText('Waiting for your OK')).toHaveCount(0) // nothing waiting, no card

    await askFor('publish_post', 'Publish the launch post on Friday')
    await page.reload()
    const card = page.getByText('Waiting for your OK')
    await expect(card).toBeVisible()
    await expect(page.getByText('Publish the launch post on Friday')).toBeVisible()
    await page.getByRole('button', { name: 'Yes, go ahead' }).click()
    await expect(card).toHaveCount(0)
    const rows = await query<{ status: string; decided_by: string }>('SELECT status, decided_by FROM approval')
    expect(rows).toEqual([{ status: 'approved', decided_by: 'Ananya Menon' }])
  })

  test('"No" rejects it, and it never comes back @smoke', async ({ page, owner }) => {
    void owner
    await askFor('message_someone_else', "Tell Priya I'm running late")
    await page.goto('/dashboard')
    await page.getByRole('button', { name: 'No' }).click()
    await expect(page.getByText('Waiting for your OK')).toHaveCount(0)
    await page.reload()
    await expect(page.getByText("Tell Priya I'm running late")).toHaveCount(0)
    expect((await query<{ status: string }>('SELECT status FROM approval'))[0].status).toBe('rejected')
  })
})
