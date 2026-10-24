// Two-channel voice (#125): something asked for on a call waits on the Approvals page with its read-back,
// and only a tap in the app confirms it. A Telegram or Slack tap, or a spoken yes, never does.
import { expect, query, test } from '../../src/fixtures'

const READBACK_SUMMARY = 'Tell Priya the meeting moved to four o’clock'

async function voiceApproval(): Promise<string> {
  const rows = await query<{ id: string }>(
    `INSERT INTO approval (id, tenant_id, agent_id, kind, payload, status, created_at)
     SELECT gen_random_uuid(), tenant_id, agent_id, 'message_someone_else',
            $1::jsonb, 'pending', now()
     FROM person LIMIT 1 RETURNING id`,
    [JSON.stringify({ origin: 'voice', summary: READBACK_SUMMARY, to: 'Priya Tan', body: 'The meeting moved to four o’clock' })],
  )
  return rows[0].id
}

test('a voice-origin approval shows what was heard and is confirmed with a tap in the app', async ({ page, owner }) => {
  void owner
  const id = await voiceApproval()
  await page.goto('/dashboard/approvals')
  const card = page.getByRole('group', { name: 'What was heard on the call' })
  await expect(card).toBeVisible()
  await expect(card).toContainText('Nothing has happened yet')
  await expect(card).toContainText('Priya Tan')
  await expect(card).toContainText('A spoken yes does not count')
  await page.screenshot({ path: process.env.VOICE_SHOT ?? 'test-results/voice-card.png', fullPage: false })
  await page.getByRole('button', { name: 'Yes, confirm it here' }).click()
  await expect(card).toHaveCount(0)
  const rows = await query<{ status: string }>('SELECT status FROM approval WHERE id = $1', [id])
  expect(rows[0].status).toBe('approved')
})
