// ADR 0014: People pages by cursor (50 at a time), VIPs first, with a skeleton that holds the layout and Load more
// from the keyboard.
import AxeBuilder from '@axe-core/playwright'
import { query } from '../src/db'
import { expect, test } from '../src/fixtures'

test('People shows 50 at a time, VIPs first, and Load more reaches everyone from the keyboard', async ({ page, api, owner }) => {
  void owner
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  await query(
    `WITH made AS (
       INSERT INTO person (id, tenant_id, agent_id, created_at, display_name, kind, relation, status, channel_prefs, notice_basis)
       SELECT gen_random_uuid(), o.tenant_id, o.agent_id, now(), 'Contact ' || lpad(g::text, 3, '0'), 'human', 'external', 'active', '{}', '{}'
       FROM person o, generate_series(1, 130) g WHERE o.id = $1::uuid
       RETURNING id, tenant_id, agent_id, display_name)
     INSERT INTO contact (id, tenant_id, agent_id, created_at, owner_person_id, person_id, relationship, vip, notes)
     SELECT gen_random_uuid(), m.tenant_id, m.agent_id, now(), $1::uuid, m.id, 'team', m.display_name = 'Contact 120', '' FROM made m`,
    [me.person.id],
  )
  const total = Number((await query<{ n: string }>(`SELECT count(*) AS n FROM contact WHERE removed_at IS NULL`))[0].n)
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/people*', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/dashboard/people')
  const heading = page.getByRole('heading', { level: 1 })
  const table = page.getByRole('table')
  await expect(heading).toBeVisible()
  const before = await table.boundingBox()
  release()
  const rows = table.getByRole('row')
  await expect(rows).toHaveCount(51) // header + 50
  const after = await table.boundingBox()
  expect(after?.y).toBe(before?.y) // the table starts where the skeleton started
  await expect(rows.nth(1)).toContainText('Contact 120') // the VIP comes first
  await expect(rows.nth(1)).toContainText('VIP')

  const more = page.getByRole('button', { name: 'Load more' })
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(rows).toHaveCount(101)
  await page.keyboard.press('Enter')
  await expect(rows).toHaveCount(total + 1)
  await expect(page.getByText('That is everyone')).toBeVisible()
  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})
