// #326: a card whose kind nothing can carry out says so before the owner taps yes.
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

async function askFor(kind: string, payload: Record<string, unknown>) {
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), $3, $4::jsonb, 'pending')`,
    [TENANT, AGENT, kind, JSON.stringify(payload)],
  )
}

test.describe('approval honesty', () => {
  test('a card with no executor says approving it will not do anything', async ({ page, owner }) => {
    void owner
    await askFor('message_someone_else', { summary: 'Cancel the Bluehost subscription via their contact page' })
    await page.goto('/dashboard')
    const note = page.getByTestId('approval-no-executor')
    await expect(note).toBeVisible()
    await expect(note).toContainText('Approving this will not do anything')
  })

  test('a card that does something says what yes does', async ({ page, owner }) => {
    void owner
    await askFor('gmail_send', { summary: 'Email Daniel, "Invoice": Thanks' })
    await page.goto('/dashboard')
    await expect(page.getByTestId('approval-on-approval')).toContainText('Sends the saved Gmail draft')
    await expect(page.getByTestId('approval-no-executor')).toHaveCount(0)
  })
})
