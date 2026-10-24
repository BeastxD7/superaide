// ADR 0014: the owner's access log pages by cursor (25 at a time) with a skeleton and Load more.
import AxeBuilder from '@axe-core/playwright'
import { query } from '../../src/db'
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'

test('the access log shows 25 visits, then Load more reaches the rest from the keyboard', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO audit_log (id, tenant_id, agent_id, created_at, actor, action, detail)
     SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now() - (g || ' minutes')::interval, 'operator', 'operator_access',
            jsonb_build_object('path', '/api/people', 'reason', 'support visit number ' || g)
     FROM person p, generate_series(1, 60) g WHERE p.id = (SELECT id FROM person ORDER BY created_at LIMIT 1)`,
  )
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/access-log*', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/dashboard/settings')
  const card = page.locator('[data-slot="card"]').filter({ hasText: 'Access log' })
  await card.scrollIntoViewIfNeeded()
  await expect(card.getByRole('status').filter({ hasText: 'Loading the access log' })).toBeAttached()
  release()
  const items = card.getByRole('list', { name: 'Access by Superaide staff' }).getByRole('listitem')
  await expect(items).toHaveCount(25)
  await expect(card.getByText('Reason given: support visit number 1', { exact: true })).toBeVisible()
  const more = card.getByRole('button', { name: 'Load more' })
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(items).toHaveCount(50)
  await page.keyboard.press('Enter')
  await expect(items).toHaveCount(60)
  await expect(card.getByText('That is every visit')).toBeVisible()
  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).include('[data-slot="card"]').analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})

test('the console Safety log pages 100 at a time with a skeleton and Load more', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO audit_log (id, tenant_id, agent_id, created_at, actor, action, detail)
     SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now() - (g || ' seconds')::interval, 'system', 'stranger_message', '{}'::jsonb
     FROM person p, generate_series(1, 230) g WHERE p.id = (SELECT id FROM person ORDER BY created_at LIMIT 1)`,
  )
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/api/audit*', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'Safety log', exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (await open.isVisible()) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of log paging')
    await open.click()
  }
  await expect(page.getByRole('status').filter({ hasText: 'Loading the log' })).toBeAttached()
  release()
  const rows = page.locator('ol.log-list > li')
  await expect(rows).toHaveCount(100)
  const more = page.getByRole('button', { name: 'Load more' })
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(rows).toHaveCount(200)
  await page.keyboard.press('Enter')
  await expect(page.getByText('That is the whole log')).toBeVisible()
  await expect(more).toHaveCount(0)
})
