// #394: the owner asked to write to a no-reply sender; the card is in Approvals with a strong warning first.
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

test('an automated recipient the owner asked for is queued with a strong warning chip', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), 'gmail_send', $3::jsonb, 'pending')`,
    [
      TENANT,
      AGENT,
      JSON.stringify({
        summary: 'To: Marlin Bank <noreply@mailer.marlin.example> · Subject: Re: Your offer · Thank you!',
        to: ['noreply@mailer.marlin.example'],
        subject: 'Re: Your offer',
        warnings: ['This address looks automated; a reply is probably not read.'],
      }),
    ],
  )
  await page.goto('/dashboard/approvals')
  const fields = page.getByTestId('approval-fields')
  await expect(fields).toContainText('noreply@mailer.marlin.example')
  await expect(fields).toContainText('Re: Your offer')
  const chip = page.getByTestId('approval-warning').first()
  await expect(chip).toContainText('This address looks automated')
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toBeVisible()
})
