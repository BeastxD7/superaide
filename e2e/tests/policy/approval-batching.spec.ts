// #408: alike, safe own-calendar requests are one card with a preview and one decision; a changed item cancels the whole
// batch; an email is never grouped; after five identical yeses Ava offers a narrow mandate. Rows are inserted sealed the
// way the server seals them (sha256 of the canonical kind + payload JSON), so the real routes and the real page run.
import { createHash } from 'node:crypto'
import { expect, query, test } from '../../src/fixtures'
import { AGENT, TENANT, queueEmail } from '../principal/_sign-helper'

// Python's json.dumps(sort_keys=True, ensure_ascii=False) for plain values, which is what approvals/binding.py hashes.
function pyDumps(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(pyDumps).join(', ')}]`
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}: ${pyDumps(o[k])}`).join(', ')}}`
  }
  return JSON.stringify(v)
}
const seal = (kind: string, payload: object) => createHash('sha256').update(pyDumps({ kind, payload })).digest('hex')

async function calendarCard(summary: string, status: 'pending' | 'approved' = 'pending', extra: object = {}): Promise<void> {
  const payload = { summary, event: { summary }, notify: false, tainted: false, ...extra }
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status, bound_hash, decided_at)
     VALUES (gen_random_uuid(), $1, $2, now(), 'calendar_create_event', $3::jsonb, $4, $5, ${status === 'approved' ? 'now()' : 'NULL'})`,
    [TENANT, AGENT, JSON.stringify(payload), status, seal('calendar_create_event', payload)],
  )
}
const statuses = async () => (await query<{ status: string }>('SELECT status FROM approval ORDER BY created_at, id')).map((r) => r.status)
const shot = async (page: import('@playwright/test').Page, name: string) => {
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}.png` })
}

test('three alike own-calendar requests are one card, and one yes decides each of them', async ({ page, owner }) => {
  void owner
  for (const s of ['Gym, Mon 7am', 'Gym, Wed 7am', 'Gym, Fri 7am']) await calendarCard(s)
  await page.goto('/dashboard/approvals')
  const batch = page.getByTestId('approval-batch')
  await expect(batch).toBeVisible()
  await expect(batch.getByTestId('approval-batch-items').getByRole('listitem')).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'No', exact: true })).toHaveCount(0) // no card of their own
  await shot(page, 'batch')
  await batch.getByRole('button', { name: 'Yes to all 3' }).click()
  await expect.poll(statuses).toEqual(['approved', 'approved', 'approved'])
})

test('a request changed after the card was shown cancels the whole batch', async ({ page, owner }) => {
  void owner
  await calendarCard('Gym, Mon 7am')
  await calendarCard('Gym, Wed 7am')
  await page.goto('/dashboard/approvals')
  await expect(page.getByTestId('approval-batch')).toBeVisible()
  await query(
    `UPDATE approval SET payload = jsonb_set(payload, '{event,summary}', '"Gym, and invite the whole team"') WHERE payload->>'summary' = 'Gym, Wed 7am'`,
  )
  await page.getByRole('button', { name: 'Yes to all 2' }).click()
  await expect(page.getByText(/changed since you looked/)).toBeVisible()
  expect(await statuses()).toEqual(['pending', 'pending'])
  await shot(page, 'batch-changed')
})

test('an email is never grouped, however many are waiting', async ({ page, owner }) => {
  void owner
  await queueEmail()
  await queueEmail()
  await page.goto('/dashboard/approvals')
  await expect(page.getByText('Waiting for your OK')).toBeVisible()
  await expect(page.getByTestId('approval-batch')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toHaveCount(2)
})

test('after five identical yeses an offer for a narrow mandate appears, and a tap signs it', async ({ page, owner }) => {
  void owner
  for (let i = 0; i < 5; i++) await calendarCard(`Gym ${i}`, 'approved')
  await page.goto('/dashboard/approvals')
  const offer = page.getByTestId('mandate-suggestion')
  await expect(offer).toContainText('Always do this kind of thing?')
  await expect(offer).toContainText('at most 25 times')
  await shot(page, 'suggestion')
  await offer.getByRole('button', { name: 'Yes, always' }).click()
  await expect(offer).toHaveCount(0)
  await expect(page.getByText('Add events to your own calendar (no guests)').first()).toBeVisible()
  expect((await query('SELECT 1 FROM mandate')).length).toBe(1)
})
