// Two contacts share the full name a stranger claimed (#492): the owner gets one card per contact in Needs you.
// Runs on any slot (E2E_SLOT=9 npx playwright test ambiguous-claims). The cards are seeded straight into the
// database exactly as the pipeline writes them (sealed approvals), so no bot is needed.
import { createHash } from 'node:crypto'
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

// Python's json.dumps(sort_keys=True, ensure_ascii=False) for a flat payload of strings and numbers.
function pyJson(o: Record<string, string | number>): string {
  return '{' + Object.keys(o).sort().map((k) => `${JSON.stringify(k)}: ${JSON.stringify(o[k])}`).join(', ') + '}'
}

async function seed(): Promise<string[]> {
  await query(
    `INSERT INTO channel_identity (id, tenant_id, agent_id, provider, tenant_scope, stable_key, trust_level, aux_handles, lifecycle, verification, risk_flags, endpoint_key)
     VALUES (gen_random_uuid(), $1, $2, 'telegram', '', '880301', 0, '{}', 'active', '{}', '{}', '')`,
    [TENANT, AGENT],
  )
  const identity = (await query('SELECT id FROM channel_identity WHERE stable_key = $1', ['880301']))[0].id as string
  const relations = ['colleague', 'client']
  const ids: string[] = []
  for (const relation of relations) {
    await query(
      `INSERT INTO person (id, tenant_id, agent_id, display_name, kind, relation, status, channel_prefs, notice_basis)
       VALUES (gen_random_uuid(), $1, $2, 'Priya Tan', 'human', $3, 'active', '{}', '{}')`,
      [TENANT, AGENT, relation],
    )
  }
  const people = (await query(`SELECT id, relation FROM person WHERE display_name = 'Priya Tan' ORDER BY relation`)) as { id: string; relation: string }[]
  for (const p of people) {
    const payload = {
      candidates: 2,
      claimed_name: 'Priya Tan',
      group: 'e2e-group',
      identity_id: identity,
      person_id: p.id,
      provider: 'telegram',
      summary: `Someone on Telegram says they are Priya Tan. 2 of your contacts have that name. Is this the one (${p.relation}, on Slack)? Send a link code to them?`,
    }
    const body = `{"kind": "identity_claim_pick", "payload": ${pyJson(payload)}}`
    await query(
      `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status, bound_hash)
       VALUES (gen_random_uuid(), $1, $2, now(), 'identity_claim_pick', $3::jsonb, 'pending', $4)`,
      [TENANT, AGENT, JSON.stringify(payload), createHash('sha256').update(body).digest('hex')],
    )
    ids.push(p.id)
  }
  return ids
}

test.describe('two contacts, one claimed name', () => {
  test('one card per contact; no on both ignores the claim and sends nothing', async ({ page, owner }) => {
    void owner
    await seed()
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard/needs-you')
    const cards = page.getByTestId('ambiguous-claim')
    await expect(cards).toHaveCount(2)
    await expect(cards.first()).toContainText('Someone on Telegram says they are Priya Tan')
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-ambiguous.png` : 'test-results/ambiguous.png' })
    await cards.first().getByRole('button', { name: 'No, not this one' }).click()
    await expect(cards).toHaveCount(1)
    await cards.first().getByRole('button', { name: 'No, not this one' }).click()
    await expect(page.getByTestId('ambiguous-claims')).toHaveCount(0)
    expect((await query('SELECT count(*)::int AS n FROM link_challenge'))[0].n).toBe(0)
    expect((await query(`SELECT status FROM approval WHERE kind = 'identity_claim_pick'`)).every((r: { status: string }) => r.status === 'rejected')).toBe(true)
  })

  test('picking one decides it and closes the other card', async ({ page, owner }) => {
    void owner
    await seed()
    await page.goto('/dashboard/needs-you')
    const cards = page.getByTestId('ambiguous-claim')
    await expect(cards).toHaveCount(2)
    await cards.first().getByRole('button', { name: 'Yes, send a code to this one' }).click()
    await expect(page.getByTestId('ambiguous-claims')).toHaveCount(0)
    const rows = (await query(`SELECT status, decided_by FROM approval WHERE kind = 'identity_claim_pick' ORDER BY status`)) as { status: string; decided_by: string }[]
    expect(rows.map((r) => r.status)).toEqual(['approved', 'rejected'])
    expect(rows[1].decided_by).toContain('another contact was picked')
    // The stranger is linked to nobody until they type the code.
    expect((await query('SELECT person_id FROM channel_identity WHERE stable_key = $1', ['880301']))[0].person_id).toBeNull()
  })

  test('the approvals list is the owner’s alone', async ({ browser }) => {
    const anonymous = await browser.newContext()
    expect((await anonymous.request.get('/api/app/approvals')).status()).toBe(401)
    await anonymous.close()
  })
})
