// What the nightly sleep job tidied, with Undo (#415). Behind SLEEP_JOB_ENABLED, so it runs only with
// E2E_SLEEP=1 (e.g. `E2E_SLEEP=1 E2E_SLOT=28 npx playwright test tidy`).
import { SLEEP } from '../../src/env'
import { expect, query, test } from '../../src/fixtures'

test.skip(!SLEEP, 'SLEEP_JOB_ENABLED is off by default; set E2E_SLEEP=1 to run these')

// One retired note (a duplicate from a file) and the note that stays, as the job leaves them.
async function seedTidy(retired: string, kept: string): Promise<void> {
  await query(
    `WITH o AS (SELECT id, tenant_id, agent_id FROM person WHERE relation = 'manager'),
     run AS (INSERT INTO consolidation_run (id, tenant_id, agent_id, owner_person_id, status, degraded, episodes_made, entries_before,
                                            retired_ids, snapshot, cost_usd, notes)
             SELECT gen_random_uuid(), tenant_id, agent_id, id, 'done', false, 0, 2, '{}', '[]', 0, '{}' FROM o RETURNING id),
     k AS (INSERT INTO memory_item (id, tenant_id, agent_id, content, subject_person_ids, source_event_ids, allowed_kind,
                                    allowed_person_ids, min_trust, category, source)
           SELECT gen_random_uuid(), tenant_id, agent_id, $2, ARRAY[id], '{}', 'persons', ARRAY[id], 2, 'about', 'chat' FROM o RETURNING id),
     r AS (INSERT INTO memory_item (id, tenant_id, agent_id, content, subject_person_ids, source_event_ids, allowed_kind,
                                    allowed_person_ids, min_trust, category, source, valid_to)
           SELECT gen_random_uuid(), tenant_id, agent_id, $1, ARRAY[id], '{}', 'persons', ARRAY[id], 2, 'about', 'document',
                  date_trunc('second', now()) FROM o RETURNING id, valid_to)
     INSERT INTO consolidation_change (id, tenant_id, agent_id, run_id, owner_person_id, kind, memory_id, kept_id, reason,
                                       decided_by, source, before, after)
     SELECT gen_random_uuid(), o.tenant_id, o.agent_id, run.id, o.id, 'duplicate', r.id, k.id,
            'Same words as another note once capitals and punctuation are ignored; the better-sourced one stays.', 'code', 'document',
            '{"retired": {"valid_to": null, "superseded_by": null}, "keeper": {"confidence": 1.0, "source_event_ids": []}}'::jsonb,
            jsonb_build_object('retired', jsonb_build_object('valid_to', to_char(r.valid_to AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'), 'superseded_by', null),
                               'keeper', jsonb_build_object('confidence', 1.0, 'source_event_ids', '[]'::jsonb))
     FROM o, run, r, k`,
    [retired, kept],
  )
}

test.describe('what Ava tidied last night', () => {
  test('lists what changed and why, and Undo brings the note back, once', async ({ page, owner }) => {
    void owner
    await seedTidy('Priya Tan joined the Lisbon offsite (from a file).', 'Priya Tan joined the Lisbon offsite.')
    await page.goto('/dashboard/notebook')
    await page.getByRole('link', { name: /What .* tidied last night/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: /tidied last night/ })).toBeVisible()
    const list = page.getByRole('list', { name: 'Tidied notes' })
    await expect(list.getByText('Priya Tan joined the Lisbon offsite (from a file).')).toBeVisible()
    await expect(list.getByText('Same note twice')).toBeVisible()
    await expect(list.getByText(/better-sourced one stays/)).toBeVisible()
    await list.getByRole('button', { name: 'Undo' }).click()
    await expect(list.getByText(/Undone/)).toBeVisible()
    await expect(list.getByRole('button', { name: 'Undo' })).toHaveCount(0)
    await page.reload()
    await expect(list.getByText(/Undone/)).toBeVisible()
    const back = await query<{ valid_to: string | null }>(`SELECT valid_to FROM memory_item WHERE content LIKE '%(from a file)%'`)
    expect(back[0]?.valid_to ?? null).toBeNull()
  })

  test('an empty log says so', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/notebook/tidy')
    await expect(page.getByText('Nothing tidied yet.')).toBeVisible()
  })

  test('the signed-out visitor is sent to log in', async ({ page, api }) => {
    void api
    await page.goto('/dashboard/notebook/tidy')
    await expect(page).toHaveURL(/\/login/)
  })
})
