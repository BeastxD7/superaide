// #668: the Outlook calendar pages fit a 390px phone.
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Outlook calendar pages fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const outlook = cards.find((c) => c.id === 'superaide/outlook-calendar')!
  await api.ok(api.post('/app/skills/superaide/outlook-calendar/install', { accepted: outlook.permissions.map((p) => p.id) }))
  const marks: Record<string, string> = {
    '': 'Ava cannot edit, move, decline or delete events',
    '/limits': 'Calendar lookups per hour',
  }
  for (const [path, text] of Object.entries(marks)) {
    await page.goto(`/dashboard/capabilities/outlook-calendar${path}`)
    await expect(page.getByText(text).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/668-outlook-calendar-${info.project.name}.png` })
})
