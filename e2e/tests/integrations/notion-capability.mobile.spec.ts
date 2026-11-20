// #669: the Notion pages fit a 390px phone.
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Notion pages fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const notion = cards.find((c) => c.id === 'superaide/notion')!
  await api.ok(api.post('/app/skills/superaide/notion/install', { accepted: notion.permissions.map((p) => p.id) }))
  const marks: Record<string, string> = {
    '': 'Ava sees only the pages you choose',
    '/limits': 'Notion lookups per hour',
  }
  for (const [path, text] of Object.entries(marks)) {
    await page.goto(`/dashboard/capabilities/notion${path}`)
    await expect(page.getByText(text).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/669-notion-${info.project.name}.png` })
})
