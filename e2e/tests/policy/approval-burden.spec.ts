// #407: how much the owner is asked. Looking at a card is counted once, answering it is counted with its time, the
// owner's "How much am I being asked?" card and the console's weekly table show the numbers, and no text is stored.
import { ADMIN_TOKEN } from '../../src/env'
import { expect, query, test } from '../../src/fixtures'
import { Admin } from '../../src/helpers/workspace'
import { queueEmail } from '../principal/_sign-helper'

type EventRow = { event: string; verdict: string | null; opened: boolean | null }
const events = () => query<EventRow>('SELECT event, verdict, opened FROM approval_event ORDER BY created_at, id')

test('a card that is looked at and then answered is counted, and the owner sees the numbers', async ({ page, owner }, info) => {
  void owner
  await queueEmail()
  await page.goto('/dashboard/approvals')
  await expect(page.getByText('Waiting for your OK')).toBeVisible()
  await expect.poll(async () => (await events()).map((e) => e.event), { timeout: 15_000 }).toEqual(['shown', 'opened'])

  await page.getByRole('button', { name: 'No', exact: true }).click()
  await expect.poll(async () => (await events()).map((e) => e.event)).toEqual(['shown', 'opened', 'decided'])
  const decided = (await events()).find((e) => e.event === 'decided')
  expect(decided).toMatchObject({ verdict: 'reject', opened: true })

  await page.reload()
  const card = page.getByTestId('burden-card')
  await expect(card).toBeVisible()
  await expect(card.getByText('How much am I being asked?')).toBeVisible()
  await expect(card).toContainText('Said yes / no')
  await expect(card.getByText('Send an email: 1')).toBeVisible()
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-burden-${info.project.name}.png`, fullPage: true })

  // Counts and times only: nothing the card said or who it was for.
  const stored = JSON.stringify(await query('SELECT * FROM approval_event'))
  for (const secret of ['jane@startup.example', 'Out tomorrow', 'startup']) expect(stored).not.toContain(secret)
})

test('the console shows the weekly trend and the route needs the operator token', async ({ page, owner }, info) => {
  void owner
  await queueEmail()
  await page.goto('/dashboard/approvals')
  await expect.poll(async () => (await events()).length, { timeout: 15_000 }).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'No', exact: true }).click()
  await expect.poll(async () => (await events()).some((e) => e.event === 'decided')).toBe(true)

  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'Approvals', exact: true }).click()
  const table = page.getByRole('region', { name: /Approval requests and answers per week/ })
  await expect(table).toBeVisible()
  await expect(table.getByRole('columnheader', { name: 'Median wait' })).toBeVisible()
  await expect(table.getByRole('cell', { name: '100%' }).first()).toBeVisible() // the one answer was a "no", and it was unread or quick
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-console-approvals-${info.project.name}.png` })

  for (const admin of [new Admin(page.request, null), new Admin(page.request, 'wrong-token')]) {
    expect((await admin.get('/approval-metrics')).status()).toBe(401)
  }
  expect((await new Admin(page.request).get('/approval-metrics?weeks=0')).status()).toBe(422)
})

test('a signed-out visitor cannot report a card as opened', async ({ page, owner, request }) => {
  void owner
  await queueEmail()
  const [{ id }] = await query<{ id: string }>("SELECT id FROM approval WHERE status = 'pending'")
  const anonymous = await request.post(`/api/app/approvals/${id}/opened`, { headers: { 'X-Requested-With': 'superaide' } })
  expect([401, 403]).toContain(anonymous.status())
  expect(await events()).toEqual([])
  void page
})
