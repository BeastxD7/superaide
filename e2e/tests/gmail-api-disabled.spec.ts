// Connecting Gmail when the Gmail API isn't enabled in the owner's Google Cloud project: the callback sends the
// browser back with ?gmail=api_disabled and the page says what to switch on (not "try again in a minute").
import { expect, test } from '../src/fixtures'

test('?gmail=api_disabled explains how to switch the Gmail API on', async ({ page, owner }) => {
  void owner
  const headers = { 'X-Requested-With': 'superaide' }
  const skills = (await (await page.request.get('/api/app/skills')).json()) as { id: string; permissions: { id: string }[] }[]
  const gmail = skills.find((s) => s.id === 'superaide/gmail')
  expect(gmail).toBeTruthy()
  const res = await page.request.post('/api/app/skills/superaide/gmail/install', {
    headers,
    data: { accepted: gmail!.permissions.map((p) => p.id) },
  })
  expect(res.status()).toBe(200)
  await page.goto('/dashboard/capabilities?gmail=api_disabled')
  const group = page.getByRole('group', { name: 'Gmail settings' })
  await expect(group).toBeVisible()
  await page.screenshot({ path: process.env.SHOT ?? 'test-results/gmail-api-disabled.png' })
  await expect(group).toContainText("The Gmail API isn't switched on for your Google project.")
  await expect(group).toContainText('search Gmail API, press Enable, then try again.')
})
