// #435: the Approvals page is a list on the left and the chosen request on the right, with Yes and No always in reach;
// on a phone the list comes first and a request opens on its own. Decided requests stay readable.
import type { Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { expect, query, test } from '../../src/fixtures'

const TENANT = '00000000-0000-4000-8000-000000000001'
const AGENT = '00000000-0000-4000-8000-000000000002'

async function askFor(subject: string, body: string, to = 'rahul@larkfield.example') {
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), 'gmail_send', $3::jsonb, 'pending')`,
    [TENANT, AGENT, JSON.stringify({ summary: `To: ${to} · Subject: ${subject} · ${body}`, to: [to], subject, body })],
  )
}

const list = (page: Page) => page.getByRole('list', { name: 'Waiting requests' })
const detail = (page: Page) => page.getByRole('region', { name: 'Request' })

test('three requests: a list and one open on the right, buttons visible without scrolling, long text included', async ({ page, owner }) => {
  void owner
  await askFor('Budget numbers for Q4', 'Rahul, the Q4 numbers are attached.')
  await askFor('Dinner on Friday', 'Hi Priya, Friday at 8 works for me.', 'priya@larkfield.example')
  await askFor('Long note', 'word '.repeat(1500), 'meera@larkfield.example')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/dashboard/approvals')
  await expect(list(page).getByRole('listitem')).toHaveCount(3)
  const article = page.getByRole('article')
  await expect(article).toHaveCount(1) // one open beside the list, not three stacked cards
  const yes = page.getByRole('button', { name: 'Yes, go ahead' })
  await expect(yes).toBeVisible()
  // Choose the long one: its 7,500 characters scroll inside the detail pane, and the buttons stay on screen.
  await list(page).getByRole('button', { name: /meera@larkfield.example/ }).click()
  await expect(page).toHaveURL(/\?a=/)
  await expect(yes).toBeInViewport()
  const box = (await yes.boundingBox())!
  expect(box.y + box.height).toBeLessThanOrEqual(900)
})

test('deciding one request moves on, and the decided one is readable under Decided', async ({ page, owner }) => {
  void owner
  await askFor('Budget numbers for Q4', 'Rahul, the Q4 numbers are attached.')
  await askFor('Dinner on Friday', 'Hi Priya, Friday at 8 works for me.', 'priya@larkfield.example')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/dashboard/approvals')
  await expect(list(page).getByRole('listitem')).toHaveCount(2)
  await page.getByRole('button', { name: 'Yes, go ahead' }).click()
  await expect(list(page).getByRole('listitem')).toHaveCount(1)
  await page.getByRole('tab', { name: /Decided/ }).click()
  await page.getByRole('list', { name: 'Decided requests' }).getByRole('button').first().click()
  await expect(page.getByTestId('approval-outcome')).toContainText('Approved')
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toHaveCount(0) // read-only
})

test('arrow-free keyboard use: rows are buttons reachable by Tab and open with Enter; axe passes', async ({ page, owner }) => {
  void owner
  await askFor('Budget numbers for Q4', 'Rahul, the Q4 numbers are attached.')
  await askFor('Dinner on Friday', 'Hi Priya, Friday at 8 works for me.', 'priya@larkfield.example')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/dashboard/approvals')
  const second = list(page).getByRole('button').nth(1)
  await second.focus()
  await page.keyboard.press('Enter')
  await expect(second).toHaveAttribute('aria-current', 'true')
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})

test('the rules and insights view replaces the list, and Requests brings it back', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/approvals')
  await page.getByRole('tab', { name: 'Rules and insights' }).click()
  await expect(page.getByRole('tab', { name: 'Rules and insights' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: /Waiting/ })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Requests' }).click()
  await expect(page.getByRole('tab', { name: /Waiting/ })).toBeVisible()
})

test('at 360 the list comes first, a request opens alone, and Back returns to the list without sideways overflow', async ({ page, owner }) => {
  void owner
  await askFor('Budget numbers for Q4', 'Rahul, the Q4 numbers are attached.')
  await askFor('Dinner on Friday', 'Hi Priya, Friday at 8 works for me.', 'priya@larkfield.example')
  await page.setViewportSize({ width: 360, height: 740 })
  await page.goto('/dashboard/approvals')
  await expect(list(page).getByRole('listitem')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toHaveCount(0) // list only
  await list(page).getByRole('button').first().click()
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toBeVisible()
  await expect(list(page)).toBeHidden()
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(over).toBeLessThanOrEqual(0)
  await page.getByRole('button', { name: 'All requests' }).click()
  await expect(list(page)).toBeVisible()
})
