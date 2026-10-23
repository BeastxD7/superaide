// The conversation summaries of the nightly sleep job (#50). Behind SLEEP_JOB_ENABLED, so it runs only with
// E2E_SLEEP=1 (e.g. `E2E_SLEEP=1 E2E_SLOT=46 npx playwright test episodes`).
import { SLEEP } from '../src/env'
import { expect, query, test } from '../src/fixtures'

test.skip(!SLEEP, 'SLEEP_JOB_ENABLED is off by default; set E2E_SLEEP=1 to run these')

async function seedEpisode(summary: string, audienceOwner = true): Promise<void> {
  await query(
    `WITH ctx AS (
       INSERT INTO context (id, tenant_id, agent_id, provider, tenant_scope, external_id, kind, audience_class, title, created_at)
       SELECT gen_random_uuid(), tenant_id, agent_id, 'telegram', '', gen_random_uuid()::text, 'dm', 'private_1to1', '', now()
       FROM person WHERE relation = 'manager' RETURNING id, tenant_id, agent_id)
     INSERT INTO episode (id, tenant_id, agent_id, owner_person_id, context_id, channel, participant_person_ids, audience_person_ids,
                          audience_has_unknown, started_at, ended_at, event_ids, summary, summary_by, hints, importance, created_at)
     SELECT gen_random_uuid(), ctx.tenant_id, ctx.agent_id, p.id, ctx.id, 'telegram', '{}',
            CASE WHEN $2 THEN ARRAY[p.id] ELSE ARRAY[gen_random_uuid()] END,
            false, now() - interval '9 hours', now() - interval '8 hours', ARRAY[gen_random_uuid(), gen_random_uuid(), gen_random_uuid()],
            $1, 'model', ARRAY['lisbon','offsite'], 0.6, now()
     FROM ctx, person p WHERE p.relation = 'manager'`,
    [summary, audienceOwner],
  )
}

test.describe('conversation summaries', () => {
  test('are listed in the notebook, only the owner own, and can be erased', async ({ page, owner }) => {
    void owner
    await seedEpisode('They planned the Lisbon offsite and booked flights.')
    await seedEpisode('A private chat between other people.', false)
    await page.goto('/dashboard/notebook')
    await page.getByRole('link', { name: /Conversations/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Conversations' })).toBeVisible()
    const list = page.getByRole('list', { name: 'Conversation summaries' })
    await expect(list.getByText('They planned the Lisbon offsite and booked flights.')).toBeVisible()
    await expect(list.getByText('3 messages')).toBeVisible()
    await expect(page.getByText('A private chat between other people.')).toHaveCount(0)
    await list.getByRole('button', { name: 'Erase this summary' }).click()
    await list.getByRole('button', { name: 'Erase summary' }).click()
    await expect(page.getByText('Nothing yet.')).toBeVisible()
    await page.reload()
    await expect(page.getByText('Nothing yet.')).toBeVisible()
  })

  test('the signed-out visitor is sent to log in', async ({ page, api }) => {
    void api
    await page.goto('/dashboard/notebook/episodes')
    await expect(page).toHaveURL(/\/login/)
  })
})
