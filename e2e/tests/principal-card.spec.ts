// Principal Card (#135): the owner's page to choose the shown name, see the cards sent and revoke them.
// On by default. The kill-flag check needs the stack with the card off:
//   E2E_CARD=0 E2E_SLOT=48 npx playwright test tests/principal-card.spec.ts --project=desktop
import { CARD_OFF } from '../src/env'
import { expect, query, test } from '../src/fixtures'

test.describe('Principal card, global kill flag off', () => {
  test.skip(!CARD_OFF, 'needs E2E_CARD=0')

  test('there is no link, the page says it is off and the API answers 404', async ({ page, api, owner }) => {
    void owner
    await page.goto('/dashboard')
    await expect(page.getByRole('link', { name: 'Principal card' })).toHaveCount(0)
    await page.goto('/dashboard/principal-card')
    await expect(page.getByText('This isn’t turned on yet.')).toBeVisible()
    expect((await api.get('/app/principal-card')).status()).toBe(404)
  })
})

test.describe('Principal card, on by default', () => {
  test.skip(CARD_OFF, 'only when the card is on')

  test('set the name, see the cards sent, revoke one and then all', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await page.getByRole('link', { name: 'Principal card' }).click()
    await expect(page.getByRole('heading', { name: 'Principal card' })).toBeVisible()
    await expect(page.getByText('No cards sent yet.')).toBeVisible()

    await expect(page.getByRole('button', { name: 'Turn the default off' })).toBeVisible() // on without doing anything
    await page.getByLabel('Your first name (optional)').fill('Ananya')
    await page.getByLabel('Initial to show after it (optional)').fill('T')
    await expect(page.getByText('People will see: Ananya T.')).toBeVisible()
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Saved.', { exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'See how it looks' })).toBeVisible()
    await expect(page.getByText('Cards shown')).toBeVisible()

    // Two cards as the channels would have noted them (a reply to a colleague on Slack and on Telegram).
    const now = Math.floor(Date.now() / 1000)
    const sent = [
      { id: 'aaaaaaaaaaaa', channel: 'slack', to: 'Priya Tan', at: now - 600, exp: now + 86400 },
      { id: 'bbbbbbbbbbbb', channel: 'telegram', to: 'Jon Lee', at: now - 60, exp: now + 86400 },
    ]
    const [scope] = await query<{ tenant_id: string; agent_id: string }>('SELECT tenant_id, agent_id FROM agent_state LIMIT 1')
    await query(
      "INSERT INTO agent_state (tenant_id, agent_id, key, value) VALUES ($1, $2, 'principal_card_sent', $3::jsonb)",
      [scope.tenant_id, scope.agent_id, JSON.stringify({ sent })],
    )
    await page.reload()
    await expect(page.getByText('Priya Tan')).toBeVisible()
    await expect(page.getByText('Jon Lee')).toBeVisible()

    await page.getByRole('button', { name: /Revoke the card sent to Priya Tan/ }).click()
    await expect(page.getByText('That card no longer opens.')).toBeVisible()
    await expect(page.getByText('revoked', { exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: /Revoke the card sent to Jon Lee/ })).toBeVisible()

    await page.getByRole('button', { name: 'Revoke all cards' }).click()
    await expect(page.getByText('Every card sent so far no longer opens.')).toBeVisible()
    await page.getByRole('button', { name: 'Turn the default off' }).click()
    await expect(page.getByText('Emails are no longer signed by default.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Turn the default on' })).toBeVisible()
  })
})
