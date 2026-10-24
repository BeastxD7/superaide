// The operator console shows an owner's messages (break-glass). Selecting conversation A (slow answer) and then B must
// never show A's rows under B: a stale answer may not land on the newer selection (#81, FE-07).
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'

const conv = (id: string, provider: string) => ({
  id,
  provider,
  kind: 'dm',
  audience_class: 'private_1to1',
  title: id,
  messages: 1,
  last_at: new Date().toISOString(),
})
const row = (id: string, context: string, content: string) => ({
  id,
  context_id: context,
  direction: 'in',
  sender: 'Someone',
  trust: 2,
  audience_size: 1,
  audience_has_unknown: false,
  content,
  at: new Date().toISOString(),
})

test('a slow answer for the first conversation never shows under the second', async ({ page, api }) => {
  void api
  await page.route('**/api/api/conversations/page*', (r) =>
    r.fulfill({ json: { items: [conv('conv-a', 'telegram'), conv('conv-b', 'slack')], next_cursor: null, has_more: false } }),
  )
  let releaseA: () => void = () => {}
  const heldA = new Promise<void>((resolve) => (releaseA = resolve))
  await page.route('**/api/api/events?context_id=conv-a*', async (r) => {
    await heldA
    await r.fulfill({ json: [row('e-a', 'conv-a', 'PRIVATE MESSAGE OF OWNER A')] })
  })
  await page.route('**/api/api/events?context_id=conv-b*', (r) => r.fulfill({ json: [row('e-b', 'conv-b', 'message of owner B')] }))

  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'Conversations', exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (await open.isVisible()) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of the console')
    await open.click()
  }
  // A is selected first (its answer is held); then B is chosen and answers at once.
  await expect(page.getByRole('status').filter({ hasText: 'Loading messages' })).toBeAttached()
  await page.getByRole('navigation', { name: 'Conversations' }).getByRole('button', { name: /slack/ }).click()
  await expect(page.getByText('message of owner B')).toBeVisible()

  releaseA() // the stale answer arrives now
  await page.waitForTimeout(500)
  await expect(page.getByText('PRIVATE MESSAGE OF OWNER A')).toHaveCount(0)
  await expect(page.getByText('message of owner B')).toBeVisible()
})
