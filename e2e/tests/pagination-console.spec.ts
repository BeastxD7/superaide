// ADR 0014 in the operator console: conversations and a thread page by cursor with skeletons and Load more, and
// the pages are reachable from the keyboard.
import AxeBuilder from '@axe-core/playwright'
import { ADMIN_TOKEN } from '../src/env'
import { query } from '../src/db'
import { expect, test } from '../src/fixtures'

async function openConsole(page: import('@playwright/test').Page, tab: string) {
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: tab, exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (await open.isVisible()) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of console paging')
    await open.click()
  }
}

test('conversations load 50 at a time with a skeleton, and the long thread loads earlier messages', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO context (id, tenant_id, agent_id, created_at, last_event_at, provider, tenant_scope, external_id, kind, audience_class, title)
     SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now(), now() - (g || ' minutes')::interval, 'telegram', '', 'tg-' || g, 'dm', 'private_1to1', ''
     FROM person p, generate_series(1, 70) g WHERE p.id = (SELECT id FROM person ORDER BY created_at LIMIT 1)`,
  )
  const big = (await query<{ id: string }>(`SELECT id FROM context WHERE external_id = 'tg-1'`))[0].id
  await query(
    `INSERT INTO event (id, tenant_id, agent_id, created_at, context_id, direction, trust_at_time, audience_person_ids, audience_has_unknown, content, meta, webhook_verified)
     SELECT gen_random_uuid(), c.tenant_id, c.agent_id, now() - (g || ' seconds')::interval, c.id, 'in', 2, '{}', false, 'thread line ' || g, '{}', true
     FROM context c, generate_series(1, 130) g WHERE c.id = $1::uuid`,
    [big],
  )
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/api/events**', async (route) => {
    await gate
    await route.continue()
  })
  const total = Number((await query<{ n: string }>(`SELECT count(*) AS n FROM context`))[0].n) // seeded plus the owner's own
  await openConsole(page, 'Conversations')
  const nav = page.getByRole('navigation', { name: 'Conversations' })
  await expect(nav.locator('button.conv')).toHaveCount(50)
  await nav.getByRole('button', { name: /telegram/ }).first().click() // tg-1, the one with the long thread
  await expect(page.getByRole('status').filter({ hasText: 'Loading messages' })).toBeAttached() // the thread is on its way
  release()
  await expect(page.getByText('thread line 1', { exact: true })).toBeVisible()
  await expect(page.getByText('thread line 100', { exact: true })).toBeAttached() // 100 newest are loaded
  await expect(page.getByText('thread line 130', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Load more' }).last().click() // the thread's footer is last in the DOM order
  await expect(page.getByText('thread line 130', { exact: true })).toBeAttached()

  const more = nav.getByRole('button', { name: 'Load more' })
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(nav.locator('button.conv')).toHaveCount(total)
  await expect(nav.getByText('All conversations')).toBeVisible()
  await page.waitForTimeout(500)
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})
