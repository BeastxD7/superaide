// The outage banner (#503): when the AI model is unavailable the owner is told, in fixed words, since when and what
// to expect; when it is back the banner is gone. The state is set the way the receptionist records it.
import { expect, query, test } from '../../src/fixtures'

async function modelHealth(status: 'ok' | 'error' | 'budget') {
  const [scope] = await query<{ tenant_id: string; agent_id: string }>('SELECT tenant_id, agent_id FROM audit_log LIMIT 1')
  const now = new Date().toISOString()
  const since = status === 'ok' ? null : now
  await query(
    `INSERT INTO agent_state (tenant_id, agent_id, key, value) VALUES ($1, $2, 'model_health', $3::jsonb)
     ON CONFLICT (tenant_id, agent_id, key) DO UPDATE SET value = EXCLUDED.value`,
    [scope.tenant_id, scope.agent_id, JSON.stringify({ status, detail: '', since, last_at: now })],
  )
}

test.describe('outage banner', () => {
  test('shows while the model is unavailable, in fixed words, and disappears when it is back', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto('/dashboard')
    await expect(page.getByTestId('degraded-banner')).toHaveCount(0)

    await modelHealth('error')
    await page.reload()
    const banner = page.getByTestId('degraded-banner')
    await expect(banner).toContainText('The AI model is unavailable')
    await expect(banner).toContainText('every message is still kept')
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-banner-1280.png` : 'test-results/banner-1280.png' })

    await page.setViewportSize({ width: 360, height: 740 })
    await expect(banner).toBeVisible()
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/after-banner-360.png` : 'test-results/banner-360.png' })

    await modelHealth('ok')
    await page.reload()
    await expect(page.getByTestId('degraded-banner')).toHaveCount(0)
  })

  test('names the spending limit as the cause when that is why', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await modelHealth('budget')
    await page.reload()
    await expect(page.getByTestId('degraded-banner')).toContainText('The spending limit is reached')
  })
})
