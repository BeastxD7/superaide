// One operator visit to People is one entry in the owner's access log (#286), and the owner's own sign-in has no
// Unlink button (#285). Set SHOT=<dir> to keep screenshots.
import { expect, test } from '../src/fixtures'
import { ADMIN_TOKEN } from '../src/env'

const shot = (name: string) => (process.env.SHOT ? `${process.env.SHOT}/${name}.png` : undefined)

test('a long visit to People is one access-log entry, and the owner cannot be unlinked', async ({ page, owner }) => {
  void owner
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'People', exact: true }).click()
  await page.getByLabel(/^Reason/).fill('sync consistency check')
  await page.getByRole('button', { name: "Open, and record it in the owner's log" }).click()
  await expect(page.getByText("Owner's own sign-in; cannot be unlinked")).toBeVisible()
  await expect(page.getByRole('button', { name: 'Unlink' })).toHaveCount(0)
  await page.waitForTimeout(12_000) // two refreshes of the screen
  await page.screenshot({ path: shot('console-people') })
  const log = (await (await page.request.get('/api/app/access-log')).json()) as { what: string }[]
  expect(log.filter((e) => e.what === '/api/people')).toHaveLength(1)
  await page.goto('/dashboard/settings')
  await expect(page.getByText('People and linked accounts').first()).toBeVisible()
  await page.screenshot({ path: shot('app-access-log') })
})
