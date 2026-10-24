// #327: an email card shows To and Subject first, and a warning chip for a recipient not seen before.
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

test('an email card leads with To and Subject and flags a new recipient', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), 'gmail_send', $3::jsonb, 'pending')`,
    [
      TENANT,
      AGENT,
      JSON.stringify({
        summary: 'To: Fernhost Support <support@fernhost.example> · Subject: Re: Your ticket · Please cancel.',
        to: ['support@fernhost.example'],
        subject: 'Re: Your ticket',
        warnings: ['New recipient: not seen before in your mail.'],
      }),
    ],
  )
  await page.goto('/dashboard')
  const fields = page.getByTestId('approval-fields')
  await expect(fields).toContainText('To')
  await expect(fields).toContainText('support@fernhost.example')
  await expect(fields).toContainText('Subject')
  await expect(fields).toContainText('Re: Your ticket')
  await expect(page.getByTestId('approval-warning')).toContainText('New recipient')
})
