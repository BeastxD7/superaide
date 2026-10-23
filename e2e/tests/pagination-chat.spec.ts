// ADR 0014: the chat transcript pages by keyset cursor, shows a skeleton that holds the layout while it loads,
// and the "Show earlier" control works from the keyboard and passes axe.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../src/fixtures'

test('the transcript shows a skeleton and the composer does not move when the messages arrive', async ({ page, owner }) => {
  void owner
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/messages', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: /Message /i })
  await expect(page.getByRole('status').filter({ hasText: 'Loading your conversation' })).toBeAttached()
  const before = await box.boundingBox()
  release()
  await expect(page.getByRole('status').filter({ hasText: 'Loading your conversation' })).toHaveCount(0)
  await expect(page.getByText(/your personal assistant/).first()).toBeVisible()
  const after = await box.boundingBox()
  expect(after?.y).toBe(before?.y) // no layout shift of the composer
  expect(after?.height).toBe(before?.height)
})

test('the skeleton is drawn where the chat bubbles will be', async ({ page, owner }) => {
  void owner
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/messages', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/dashboard/chat')
  const skeleton = page.getByTestId('skeleton-agent-bubble').first()
  await expect(skeleton).toBeVisible()
  const placeholder = await skeleton.boundingBox()
  release()
  const real = page.getByTestId('agent-bubble').first()
  await expect(real).toBeVisible()
  const bubble = await real.boundingBox()
  // Ava's bubbles start at the same left edge (after the 32px avatar column), so nothing slides sideways.
  expect(Math.round(placeholder!.x)).toBe(Math.round(bubble!.x))
  expect(placeholder!.height).toBeGreaterThanOrEqual(44) // at least one line of text plus padding, like a real bubble
})

test('earlier messages load by cursor, from the keyboard, and the page is accessible', async ({ page, api, owner }) => {
  void owner
  test.setTimeout(180_000)
  for (let i = 1; i <= 101; i++) await api.ok(api.post('/app/chat', { text: `cursor probe ${i}` }))

  const first = await api.get('/app/messages?limit=50')
  expect(first.headers()['x-has-more']).toBe('true')
  const cursor = first.headers()['x-next-cursor']
  expect(cursor).toBeTruthy()
  const second = await (await api.get(`/app/messages?limit=50&cursor=${encodeURIComponent(cursor)}`)).json()
  const firstRows = await first.json()
  expect(second).toHaveLength(50)
  expect(new Set([...second, ...firstRows].map((m: { id: string }) => m.id)).size).toBe(100) // no overlap
  expect((await api.get('/app/messages?cursor=tampered')).status()).toBe(422)

  await page.goto('/dashboard/chat')
  await expect(page.getByText('cursor probe 101', { exact: true })).toBeVisible()
  const earlier = page.getByRole('button', { name: 'Show earlier messages' })
  await expect(earlier).toBeVisible()
  await earlier.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('cursor probe 1', { exact: true })).toBeAttached()
  await expect(earlier).toHaveCount(0) // reached the start: the control goes away
  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).analyze()
  expect(results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
})
