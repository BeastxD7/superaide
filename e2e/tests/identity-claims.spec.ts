// "Who is this?" (#93): an unknown account named like a contact is a stranger and the owner is asked.
// Behind IDENTITY_CLAIM_REVIEW, so it runs only with E2E_CLAIMS=1 (E2E_CLAIMS=1 E2E_SLOT=46 npx playwright test identity-claims).
import { CLAIMS } from '../src/env'
import { expect, query, test } from '../src/fixtures'

test.skip(!CLAIMS, 'IDENTITY_CLAIM_REVIEW is off by default; set E2E_CLAIMS=1 to run these')

// What the pipeline would have stored for an impostor called "Priya Tan" on Telegram, written straight into
// the database so the test needs no bot: a contact, an unlinked account and the open question.
async function seedClaim(): Promise<void> {
  const first = new Date(Date.now() - 20 * 60_000).toISOString()
  await query(
    `INSERT INTO person (id, tenant_id, agent_id, display_name, kind, relation, status, channel_prefs, notice_basis)
     SELECT gen_random_uuid(), tenant_id, agent_id, 'Priya Tan', 'human', 'contact', 'active', '{}', '{}' FROM person WHERE relation = 'manager'`,
  )
  await query(
    `INSERT INTO channel_identity (id, tenant_id, agent_id, provider, tenant_scope, stable_key, trust_level, aux_handles, lifecycle, verification, risk_flags, endpoint_key)
     SELECT gen_random_uuid(), tenant_id, agent_id, 'telegram', '', '880001', 1, '{"display_name":"Priya Tan","username":"priya_t"}', 'active', '{}', '{}', ''
     FROM person WHERE relation = 'manager'`,
  )
  await query(
    `INSERT INTO identity_claim (id, tenant_id, agent_id, identity_id, matched_person_id, claimed_name, facts, status, asks)
     SELECT gen_random_uuid(), i.tenant_id, i.agent_id, i.id, p.id, 'Priya Tan',
            '{"display_name":"Priya Tan","provider":"telegram","platform_user_id":"880001","handle":"priya_t","phone":"","first_message_at":"${first}","excerpt":"Hi, I need Ananya''s calendar for next week."}'::jsonb,
            'open', 1
     FROM channel_identity i, person p WHERE i.stable_key = '880001' AND p.display_name = 'Priya Tan'`,
  )
}

test.describe('who is this', () => {
  test('shows what the platform told us and linking happens only on the owner’s answer', async ({ page, owner }) => {
    void owner
    await seedClaim()
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard/needs-you')
    const card = page.getByTestId('identity-claim')
    await expect(card.getByText('Someone is asking about you and says they are Priya Tan. Do you know them?')).toBeVisible()
    await expect(card.getByText('880001')).toBeVisible()
    await expect(card.getByText('@priya_t')).toBeVisible()
    await expect(card.getByText(/calendar for next week/)).toBeVisible()
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-claims.png` : 'test-results/claims.png' })
    // Nothing is linked yet.
    expect((await query('SELECT person_id FROM channel_identity WHERE stable_key = $1', ['880001']))[0].person_id).toBeNull()
    await card.getByRole('button', { name: /Yes, that.s Priya Tan/ }).click()
    await expect(page.getByTestId('identity-claim')).toHaveCount(0)
    expect((await query('SELECT person_id FROM channel_identity WHERE stable_key = $1', ['880001']))[0].person_id).not.toBeNull()
  })

  test('“No” keeps the account a stranger, and the list is the owner’s alone', async ({ page, owner, api, browser }) => {
    void owner
    await seedClaim()
    await page.goto('/dashboard/needs-you')
    await page.getByTestId('identity-claim').getByRole('button', { name: /No \/ don.t know/ }).click()
    await expect(page.getByTestId('identity-claim')).toHaveCount(0)
    expect((await query('SELECT person_id FROM channel_identity WHERE stable_key = $1', ['880001']))[0].person_id).toBeNull()
    expect((await query('SELECT status FROM identity_claim'))[0].status).toBe('stranger')
    void api
    const anonymous = await browser.newContext()
    const r = await anonymous.request.get('/api/app/identity-claims')
    expect(r.status()).toBe(401)
    await anonymous.close()
  })
})
