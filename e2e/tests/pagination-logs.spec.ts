// ADR 0014: the owner's access log pages by cursor (25 at a time) with a skeleton and Load more.
import AxeBuilder from '@axe-core/playwright'
import { query } from '../src/db'
import { expect, test } from '../src/fixtures'

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
