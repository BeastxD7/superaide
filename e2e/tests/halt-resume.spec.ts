// Ava halted from chat (halt word, breaker, witness) is paused per instance. The app shows a Resume button for it, and
// the operator console's Resume lifts it too (issue #281). Set SHOT=<dir> to keep screenshots.
import { expect, query, test } from '../src/fixtures'
import { ADMIN_TOKEN } from '../src/env'

const shot = (name: string) => (process.env.SHOT ? `${process.env.SHOT}/${name}.png` : undefined)

async function freezeOwnersAva() {
  const [row] = await query<{ tenant_id: string; id: string }>('SELECT tenant_id, id FROM agent_instance LIMIT 1')
  await query(
    `INSERT INTO agent_state (tenant_id, agent_id, key, value) VALUES ($1, $2, 'frozen', '{"on": true}')
     ON CONFLICT (tenant_id, agent_id, key) DO UPDATE SET value = '{"on": true}'`,
    [row!.tenant_id, row!.id],
  )
}

test('the owner resumes Ava herself after a halt', async ({ page, owner }) => {
  void owner
  await freezeOwnersAva()
  await page.goto('/dashboard')
  await expect(page.getByText(/is paused, so nobody gets a reply until you resume her/)).toBeVisible()
  await page.screenshot({ path: shot('app-paused') })
  await page.getByRole('button', { name: /^Resume / }).click()
  await expect(page.getByText(/is paused/)).toHaveCount(0)
  await page.screenshot({ path: shot('app-resumed') })
  const me = await page.request.get('/api/app/me')
  expect(((await me.json()) as { paused: boolean }).paused).toBe(false)
})

test('the console Resume lifts a halt too', async ({ page, owner }) => {
  void owner
  await freezeOwnersAva()
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await expect(page.getByText('Paused: no model calls, no replies')).toBeVisible()
  await page.getByRole('button', { name: /^Resume / }).click()
  await expect(page.getByText('Paused: no model calls, no replies')).toHaveCount(0)
  await page.reload()
  await expect(page.getByText('Paused: no model calls, no replies')).toHaveCount(0)
  await page.screenshot({ path: shot('console-resumed') })
  const me = await page.request.get('/api/app/me')
  expect(((await me.json()) as { paused: boolean }).paused).toBe(false)
})
