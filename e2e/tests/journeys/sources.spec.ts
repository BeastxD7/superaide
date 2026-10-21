// Every answer can show where it came from: numbered sources under Ava's reply.
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

test('an answer with sources lists them, with the words that were used', async ({ page, owner }) => {
  void owner
  const [ctx] = await query<{ id: string }>(`SELECT id FROM context WHERE external_id LIKE 'web:%' LIMIT 1`)
  const sources = [
    { n: 1, uri: 'ava://memory/1', kind: 'memory', title: 'Note · about', cited_text: 'flight to Bangalore is on Friday' },
    { n: 2, uri: 'https://example.com/sfo', kind: 'web', title: 'SFO delays today', cited_text: 'No delays reported' },
  ]
  await query(
    `INSERT INTO event (id, tenant_id, agent_id, created_at, context_id, direction, trust_at_time,
       audience_person_ids, audience_has_unknown, content, meta, webhook_verified)
     VALUES (gen_random_uuid(), $1, $2, now(), $3, 'out', 0, '{}', false, $4, $5::jsonb, true)`,
    [TENANT, AGENT, ctx.id, 'Your flight is on Friday [1], and there are no delays at SFO [2].', JSON.stringify({ sources })],
  )
  await page.goto('/dashboard/chat')
  await expect(page.getByText('Your flight is on Friday [1], and there are no delays at SFO [2].')).toBeVisible()
  await page.getByText('Sources (2)').click()
  const list = page.getByRole('list', { name: 'Sources' })
  await expect(list.getByText(/flight to Bangalore is on Friday/)).toBeVisible()
  const link = list.getByRole('link', { name: 'SFO delays today' })
  await expect(link).toHaveAttribute('href', 'https://example.com/sfo')
  await expect(link).toHaveAttribute('rel', /noopener/)
})
