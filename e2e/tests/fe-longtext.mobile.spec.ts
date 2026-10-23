// A 100-character unbroken string (a URL) in a reminder must not push the phone layout sideways (#81, FE-18).
import { expect, test } from '../src/fixtures'
import { expectNoSidewaysScroll } from '../src/helpers/workspace'

const LONG = `https://example.com/${'a'.repeat(80)}`

test('a reminder with a very long URL fits a phone screen', async ({ page, api, owner }) => {
  void owner
  await api.ok(api.post('/app/reminders', { text: `Read ${LONG} tomorrow at 9am` }))
  await page.goto('/dashboard/reminders')
  await expect(page.getByText(LONG, { exact: false }).first()).toBeVisible()
  await expectNoSidewaysScroll(page)
})
