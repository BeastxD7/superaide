// ADR 0014: the notebook pages by cursor (50 at a time), shows a skeleton that holds the layout, searches every
// note on the server, and Load more works from the keyboard and passes axe.
import AxeBuilder from '@axe-core/playwright'
import { query } from '../src/db'
import { expect, test } from '../src/fixtures'

async function seedNotes(api: { get(path: string): Promise<{ json(): Promise<unknown> }> }, n: number, word = 'probe') {
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  await query(
    `INSERT INTO memory_item (id, tenant_id, agent_id, created_at, content, category, subject_person_ids, source_event_ids, allowed_kind, allowed_person_ids, min_trust)
     SELECT gen_random_uuid(), p.tenant_id, p.agent_id, now() - (g || ' minutes')::interval, $2 || ' ' || g, 'about', ARRAY[p.id], '{}'::uuid[], 'persons', ARRAY[p.id], 2
     FROM person p, generate_series(1, $3::int) g WHERE p.id = $1::uuid`,
    [me.person.id, word, n],
  )
}

test('the notebook shows a skeleton, then 50 notes, and Load more reaches the rest from the keyboard', async ({ page, api, owner }) => {
  void owner
  await seedNotes(api, 120)
  const total = Number((await query<{ n: string }>(`SELECT count(*) AS n FROM memory_item WHERE valid_to IS NULL`))[0].n) // plus the owner's own starter notes
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/notebook**', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/dashboard/notebook')
  const heading = page.getByRole('heading', { level: 1 })
  const skeleton = page.getByRole('status').filter({ hasText: 'Loading your notebook' })
  await expect(skeleton).toBeAttached()
  const before = await heading.boundingBox()
  release()
  await expect(skeleton).toHaveCount(0)
  await expect(page.getByText('probe 1', { exact: true })).toBeVisible()
  const after = await heading.boundingBox()
  expect(after?.y).toBe(before?.y) // the page header does not move when the notes arrive
  await expect(page.locator('li[id^="note-"]')).toHaveCount(50)

  const more = page.getByRole('button', { name: 'Load more' })
  await more.focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('li[id^="note-"]')).toHaveCount(100)
  await page.keyboard.press('Enter')
  await expect(page.locator('li[id^="note-"]')).toHaveCount(total)
  await expect(page.getByText('That is every note')).toBeVisible()
  await expect(more).toHaveCount(0)

  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})

test('searching asks the server, so an old note is found without loading everything', async ({ page, api, owner }) => {
  void owner
  await seedNotes(api, 130)
  await query(`UPDATE memory_item SET content = 'my passport is in the blue drawer' WHERE content = 'probe 130'`)
  await page.goto('/dashboard/notebook')
  await expect(page.locator('li[id^="note-"]')).toHaveCount(50)
  await expect(page.getByText('my passport is in the blue drawer')).toHaveCount(0) // the oldest, not loaded yet
  await page.getByRole('textbox', { name: 'Search the notebook' }).fill('passport')
  await expect(page.getByText('my passport is in the blue drawer')).toBeVisible()
  await expect(page.locator('li[id^="note-"]')).toHaveCount(1)
  await expect(page.getByText('That is every note')).toBeVisible()
  await page.getByRole('textbox', { name: 'Search the notebook' }).fill('zzz-nothing')
  await expect(page.getByText('Nothing matches that search.')).toBeVisible()
})

test('a link to a note on a later page loads pages until it is found', async ({ page, api, owner }) => {
  void owner
  await seedNotes(api, 120)
  const rows = await query<{ id: string }>(`SELECT id FROM memory_item WHERE content = 'probe 110'`)
  await page.goto(`/dashboard/notebook#note-${rows[0].id}`)
  await expect(page.locator(`#note-${rows[0].id}`)).toBeVisible()
})
