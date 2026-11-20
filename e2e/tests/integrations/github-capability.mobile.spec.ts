// #669: the GitHub pages fit a 390px phone.
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the GitHub pages fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const github = cards.find((c) => c.id === 'superaide/github')!
  await api.ok(api.post('/app/skills/superaide/github/install', { accepted: github.permissions.map((p) => p.id) }))
  const marks: Record<string, string> = {
    '': 'Ava sees only the repositories you choose',
    '/limits': 'GitHub lookups per hour',
  }
  for (const [path, text] of Object.entries(marks)) {
    await page.goto(`/dashboard/capabilities/github${path}`)
    await expect(page.getByText(text).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-github-${info.project.name}.png` })
})
