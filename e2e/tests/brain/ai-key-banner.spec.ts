// The owner is told in the app when the AI cannot be used for a configuration reason (#633): a banner with what to do
// and a link to the AI model page; gone once it is fixed. State is set the way the pipeline records it.
import { expect, query, test } from '../../src/fixtures'

async function setState(key: string, value: object) {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>('SELECT tenant_id, agent_id FROM audit_log LIMIT 1')
  await query(
    `INSERT INTO agent_state (tenant_id, agent_id, key, value) VALUES ($1, $2, $3, $4::jsonb)
     ON CONFLICT (tenant_id, agent_id, key) DO UPDATE SET value = EXCLUDED.value`,
    [scope.tenant_id, scope.agent_id, key, JSON.stringify(value)],
  )
}

const shot = (name: string) => (process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}.png` : `test-results/${name}.png`)

test.describe('AI key banner', () => {
  test('credit too low: says so, links to the AI model page, and goes away when cleared', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard')
    await expect(page.getByTestId('ai-key-banner')).toHaveCount(0)

    await setState('ai_trouble', { reason: 'credit_low', provider: 'anthropic', at: new Date().toISOString() })
    await page.reload()
    const banner = page.getByTestId('ai-key-banner')
    await expect(banner).toContainText('out of credit')
    await expect(banner).not.toContainText(/sk-ant|anthropic/i)
    await page.screenshot({ path: shot('ai-banner-1280') })

    await page.setViewportSize({ width: 360, height: 740 })
    await expect(banner).toBeVisible()
    await page.screenshot({ path: shot('ai-banner-360') })

    await banner.getByRole('link').click()
    await expect(page).toHaveURL(/\/dashboard\/models$/)

    await setState('ai_trouble', {})
    await page.goto('/dashboard')
    await expect(page.getByTestId('ai-key-banner')).toHaveCount(0)
  })

  test('a rejected key and a reached spend cap each get their own words', async ({ page, owner }) => {
    void owner
    await setState('channel_health:ai:anthropic', { status: 'auth_failed', last_error: 'key rejected', since: new Date().toISOString() })
    await page.goto('/dashboard')
    await expect(page.getByTestId('ai-key-banner')).toHaveAttribute('data-reason', 'key_invalid')
    await expect(page.getByTestId('ai-key-banner')).toContainText('rejected')

    await setState('channel_health:ai:anthropic', { status: 'ok' })
    await setState('ai_trouble', { reason: 'spend_cap', provider: 'anthropic', at: new Date().toISOString() })
    await page.reload()
    await expect(page.getByTestId('ai-key-banner')).toHaveAttribute('data-reason', 'spend_cap')
    await expect(page.getByTestId('ai-key-banner')).toContainText('spending cap')
  })
})
