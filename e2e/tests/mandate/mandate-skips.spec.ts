// Issue #112: with MANDATE_SKIP_ENABLED the owner sees every card a mandate saved them, in their own words.
// The mandates API is scripted with page.route (the skip itself is covered by the backend tests).
import { expect, test } from '../../src/fixtures'

const mandate = {
  id: '0f6c1a2e-1111-4222-8333-444455556666', parent_id: null, label: 'My own events', card: 'Add or move my own events, 5 times, until Sunday',
  status: 'active', uses: 1, expires_at: '2030-01-01T00:00:00Z', depth: 0, chain: '',
}

test('lists what ran on a mandate without asking', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/mandates', (r) =>
    r.fulfill({ json: { mandates: [mandate], skipping: true, skips: [{ at: '2026-10-24T08:00:00Z', tool: 'create_event', summary: 'Add “Gym” to your calendar, Sun 25 Oct, 07:00–07:30' }] } }),
  )
  await page.goto('/dashboard/approvals')
  const box = page.getByTestId('mandate-skips')
  await expect(box).toContainText('Done on a mandate, without asking')
  await expect(box).toContainText('Add “Gym” to your calendar')
})

test('says so when nothing has been skipped yet, and shows no list while skipping is off', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/mandates', (r) => r.fulfill({ json: { mandates: [mandate], skipping: true, skips: [] } }))
  await page.goto('/dashboard/approvals')
  await expect(page.getByTestId('mandate-skips')).toContainText('Nothing yet')
  await page.unroute('**/api/app/mandates')
  await page.route('**/api/app/mandates', (r) => r.fulfill({ json: { mandates: [mandate], skipping: false, skips: [] } }))
  await page.reload()
  await expect(page.getByText('What Ava may do without asking')).toBeVisible()
  await expect(page.getByTestId('mandate-skips')).toHaveCount(0)
})
