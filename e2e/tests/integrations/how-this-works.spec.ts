// #433: the rules that are fixed on purpose are shown read-only ("How this works") next to the ones the owner can change.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../../src/fixtures'

async function install(api: { ok: <T>(p: Promise<unknown>) => Promise<T>; get: (u: string) => Promise<unknown>; post: (u: string, b?: unknown) => Promise<unknown> }, id: string) {
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills') as Promise<unknown>)
  const card = cards.find((c) => c.id === id)!
  await api.ok(api.post(`/app/skills/${id}/install`, { accepted: card.permissions.map((p) => p.id) }) as Promise<unknown>)
}

test('Gmail, Calendar and Connections explain their fixed rules', async ({ page, api, owner }, info) => {
  void owner
  await install(api as never, 'superaide/gmail')
  await install(api as never, 'superaide/google-calendar')
  await page.goto('/dashboard/capabilities/gmail')
  const gmail = page.getByRole('region', { name: 'How this works' })
  await expect(gmail).toContainText('Nothing is sent without your yes')
  await expect(gmail).toContainText('Email is information, never instructions')
  const axe = await new AxeBuilder({ page }).include('[aria-label="How this works"]').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-how-gmail-${info.project.name}.png` })
  await page.goto('/dashboard/capabilities/calendar')
  await expect(page.getByRole('region', { name: 'How this works' })).toContainText('waits for your yes')
  await install(api as never, 'superaide/web-research')
  await page.goto('/dashboard/capabilities/web')
  await expect(page.getByRole('region', { name: 'How this works' })).toContainText('never instructions')
  await page.goto('/dashboard/connections')
  await expect(page.getByRole('region', { name: 'How this works' })).toContainText('never treated as you')
})
