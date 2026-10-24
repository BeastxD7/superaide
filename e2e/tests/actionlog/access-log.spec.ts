// Owners see when someone at Superaide opened their Ava's content (ADR 0009 T8): the operator
// console can only do that with a reason, and Settings → Access log lists every time.
import { expect, test } from '../../src/fixtures'
import { ADMIN_TOKEN } from '../../src/env'

test("an operator's break-glass look shows in the owner's Access log, with the reason", async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/settings')
  await expect(page.getByText('Access log', { exact: true })).toBeVisible()
  await expect(page.getByText("No one at Superaide has opened your assistant's content.")).toBeVisible()

  const headers = { Authorization: `Bearer ${ADMIN_TOKEN}` }
  // Without a reason the console gets nothing.
  expect((await page.request.get('/api/api/memories', { headers })).status()).toBe(403)
  const r = await page.request.get('/api/api/memories', {
    headers: { ...headers, 'X-Operator-Reason': 'checking a reported reminder' },
  })
  expect(r.ok()).toBeTruthy()

  await page.reload()
  const log = page.getByRole('list', { name: 'Access by Superaide staff' })
  await expect(log).toContainText('Notebook')
  await expect(log).toContainText('Reason given: checking a reported reminder')
})
