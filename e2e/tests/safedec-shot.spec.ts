import { test, expect, query } from '../src/fixtures'

test('shot', async ({ page, owner }) => {
  const [r] = await query<{ tenant_id: string; agent_id: string }>('SELECT tenant_id, agent_id FROM person LIMIT 1')
  const payload = {
    summary: 'Email Daniel <daniel@example.com>, "Friday": See you Friday at 10.',
    notes: ['Worked 3 of 8 earlier runs of this kind (n=8, 38%). Odds are low, so I will prepare it and you do the last step.'],
  }
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, kind, payload, status) VALUES (gen_random_uuid(), $1, $2, 'gmail_send', $3::jsonb, 'pending')`,
    [r.tenant_id, r.agent_id, JSON.stringify(payload)],
  )
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/dashboard')
  await expect(page.getByText('Waiting for your OK')).toBeVisible()
  await page.screenshot({ path: process.env.SHOT_PATH!, fullPage: false })
})
