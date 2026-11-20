// #669: the Linear pages fit a 390px phone.
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Linear pages fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const linear = cards.find((c) => c.id === 'superaide/linear')!
  await api.ok(api.post('/app/skills/superaide/linear/install', { accepted: linear.permissions.map((p) => p.id) }))
  const marks: Record<string, string> = {
    '': 'Ava sees only the teams you choose',
    '/limits': 'Linear lookups per hour',
  }
  for (const [path, text] of Object.entries(marks)) {
    await page.goto(`/dashboard/capabilities/linear${path}`)
    await expect(page.getByText(text).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-linear-${info.project.name}.png` })
})
