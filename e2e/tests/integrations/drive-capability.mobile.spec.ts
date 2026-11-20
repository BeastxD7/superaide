// #667: the Google Drive pages fit a 390px phone.
import { expect, test } from '../../src/fixtures'
import { expectNoHorizontalScroll } from '../../src/helpers/accounts'

test.use({ viewport: { width: 390, height: 844 } })

test('the Google Drive pages fit 390px', async ({ page, api, owner }, info) => {
  void owner
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills'))
  const drive = cards.find((c) => c.id === 'superaide/google-drive')!
  await api.ok(api.post('/app/skills/superaide/google-drive/install', { accepted: drive.permissions.map((p) => p.id) }))
  const marks: Record<string, string> = {
    '': 'Reading is for you only, in your own chat',
    '/limits': 'Drive lookups per hour',
    '/drafts': "Google Drive isn't connected yet",
  }
  for (const [path, text] of Object.entries(marks)) {
    await page.goto(`/dashboard/capabilities/drive${path}`)
    await expect(page.getByText(text).first()).toBeVisible()
    await expectNoHorizontalScroll(page)
  }
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/667-drive-${info.project.name}.png` })
})
