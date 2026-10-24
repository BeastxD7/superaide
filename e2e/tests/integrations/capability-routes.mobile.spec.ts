// ci-module: integrations
// #441 at 390px: the capability pages fit, the sub-navigation scrolls inside its own row, targets are 44px.
import { expect, test } from '../../src/fixtures'
import { expectNoSidewaysScroll } from '../../src/helpers/workspace'

test.use({ viewport: { width: 390, height: 780 } })

test('Gmail overview and Reading fit 390px with 44px tabs', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const gmail = cards.find((c) => c.id === 'superaide/gmail')!
  await api.ok(api.post('/app/skills/superaide/gmail/install', { accepted: gmail.permissions.map((p) => p.id) }))
  for (const path of ['/dashboard/capabilities/gmail', '/dashboard/capabilities/gmail/reading']) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expectNoSidewaysScroll(page)
    const tab = page.getByRole('navigation', { name: 'Gmail settings' }).getByRole('link').first()
    expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${path.split('/').slice(3).join('-')}-${info.project.name}.png` })
  }
})
