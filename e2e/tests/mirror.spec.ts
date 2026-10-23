// "The Mirror" (#54): a weekly note of patterns with receipts. Behind MIRROR_ENABLED, so it runs only with
// E2E_MIRROR=1 (e.g. `E2E_MIRROR=1 E2E_SLOT=45 npx playwright test mirror`).
import { MIRROR } from '../src/env'
import { expect, query, test } from '../src/fixtures'

test.skip(!MIRROR, 'MIRROR_ENABLED is off by default; set E2E_MIRROR=1 to run these')

async function seedReminders(): Promise<void> {
  for (const weeksAgo of [0, 1, 2, 3]) {
    await query(
      `INSERT INTO intention (id, tenant_id, agent_id, owner_person_id, kind, text, due_at, timezone, status, source, channel,
                              delivered_via, created_at)
       SELECT gen_random_uuid(), tenant_id, agent_id, id, 'reminder', 'Send the investor update', now(), 'UTC', 'done', 'chat',
              'default', '{}', now() - make_interval(weeks => $1)
       FROM person WHERE relation = 'manager'`,
      [weeksAgo],
    )
  }
}

test.describe('the mirror', () => {
  test('is off until the owner opts in, then shows a finding with receipts', async ({ page, owner }) => {
    void owner
    await seedReminders()
    await page.goto('/dashboard/notebook')
    await page.getByRole('link', { name: /The Mirror/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'The Mirror' })).toBeVisible()
    await expect(page.getByText('Off for now')).toBeVisible()
    await page.getByRole('button', { name: 'Turn on the Mirror' }).click()
    await expect(page.getByText(/You set a reminder for .Send the investor update. 4 times across 4 weeks/)).toBeVisible()
    await page.getByText('Show the receipts (4)').click()
    await expect(page.getByText(/“Send the investor update”, set .*\(done\)/).first()).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open it' })).toHaveAttribute('href', '/dashboard/reminders')
  })

  test("'Don't mention this again' is remembered", async ({ page, owner, api }) => {
    void owner
    await seedReminders()
    await api.post('/app/mirror/settings', { enabled: true })
    await page.goto('/dashboard/notebook/mirror')
    await page.getByRole('button', { name: "Don't mention this again" }).click()
    await expect(page.getByText('Won’t be mentioned again')).toBeVisible()
    await page.reload()
    await expect(page.getByText('Won’t be mentioned again')).toBeVisible()
    const mirror = await api.ok<{ muted: number }>(api.get('/app/mirror'))
    expect(mirror.muted).toBe(1)
  })

  test('turning it off deletes the notes and a stranger is refused', async ({ page, owner, api }) => {
    void owner
    await seedReminders()
    await api.post('/app/mirror/settings', { enabled: true })
    await page.goto('/dashboard/notebook/mirror')
    await expect(page.getByText(/You set a reminder/)).toBeVisible()
    await page.getByRole('button', { name: 'Turn off the Mirror' }).click()
    await page.getByRole('button', { name: 'Turn off and delete' }).click()
    await expect(page.getByText('Off for now')).toBeVisible()
    expect(await query('SELECT 1 FROM mirror_note')).toHaveLength(0)
  })

  test('signed out, the Mirror is refused', async ({ api }) => {
    expect((await api.get('/app/mirror')).status()).toBe(401)
  })
})
