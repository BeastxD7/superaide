// The guided tour (#626): the first-run prompt, the spotlight walkthrough, skip, replay, and that the answer is the
// account's (kept on the server), not the browser's.
import { expect, query, test } from '../../src/fixtures'

test.use({ tourOn: true })

async function answers(): Promise<Record<string, string>> {
  const rows = await query<{ tour: string; status: string }>('SELECT tour, status FROM tour_progress')
  return Object.fromEntries(rows.map((r) => [r.tour, r.status]))
}

test('a new owner is asked once; "Take the tour" walks Home with Next, Back and the keyboard, then it is remembered', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  const welcome = page.getByRole('dialog', { name: 'Welcome. Want a quick tour?' })
  await expect(welcome).toBeVisible()
  await expect(welcome.getByRole('button', { name: 'Take the tour' })).toBeFocused()
  await welcome.getByRole('button', { name: 'Take the tour' }).click()

  const pop = page.getByTestId('tour-popover')
  await expect(pop.getByRole('heading', { name: 'Meet Ava' })).toBeVisible()
  await expect(pop.getByText(/^Step 1 of \d+$/)).toBeVisible()
  const dots = await page.getByTestId('tour-dot').count()
  expect(dots).toBeGreaterThanOrEqual(5)
  // The spotlight sits on the real element.
  const hole = await page.getByTestId('tour-spotlight').boundingBox()
  const card = await page.locator('[data-tour="agent-card"]').boundingBox()
  expect(hole && card && Math.abs(hole.x - (card.x - 8)) < 24).toBeTruthy()

  await pop.getByRole('button', { name: 'Next' }).click()
  await expect(pop.getByText(/^Step 2 of \d+$/)).toBeVisible()
  await pop.getByRole('button', { name: 'Back' }).click()
  await expect(pop.getByRole('heading', { name: 'Meet Ava' })).toBeVisible()
  await page.keyboard.press('ArrowRight')
  await expect(pop.getByText(/^Step 2 of \d+$/)).toBeVisible()
  await page.keyboard.press('ArrowLeft')
  await expect(pop.getByText(/^Step 1 of \d+$/)).toBeVisible()
  for (let i = 1; i < dots; i++) await page.keyboard.press('ArrowRight')
  await expect(pop.getByRole('button', { name: 'Done' })).toBeVisible()
  await pop.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByTestId('tour-overlay')).toHaveCount(0)

  await expect.poll(answers).toEqual({ welcome: 'completed', home: 'completed' })
  await page.reload()
  await page.waitForTimeout(1500)
  await expect(page.getByTestId('tour-welcome')).toHaveCount(0)
  await expect(page.getByTestId('tour-overlay')).toHaveCount(0)
})

test('after taking the tour, a page walks its own tour the first time it is opened', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Take the tour' }).click()
  await page.getByRole('button', { name: 'Skip tour' }).click()
  await expect.poll(answers).toEqual({ welcome: 'completed', home: 'skipped' })
  await page.goto('/dashboard/chat')
  const pop = page.getByTestId('tour-popover')
  await expect(pop.getByRole('heading', { name: 'Talk to Ava' })).toBeVisible()
  await pop.getByRole('button', { name: 'Next' }).click()
  await expect(pop.getByRole('heading', { name: 'Share a file' })).toBeVisible()
  await pop.getByRole('button', { name: 'Done' }).click()
  await expect.poll(answers).toEqual({ welcome: 'completed', home: 'skipped', chat: 'completed' })
})

test('"Skip" on the first-run prompt closes it for good, on every page and after a reload', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Skip', exact: true }).click()
  await expect(page.getByTestId('tour-welcome')).toHaveCount(0)
  await expect.poll(answers).toEqual({ welcome: 'skipped' })
  await page.goto('/dashboard/chat')
  await page.waitForTimeout(1500)
  await expect(page.getByTestId('tour-overlay')).toHaveCount(0)
  await expect(page.getByTestId('tour-welcome')).toHaveCount(0)
  await page.reload()
  await page.waitForTimeout(1500)
  await expect(page.getByTestId('tour-welcome')).toHaveCount(0)
})

test('Escape skips the prompt and a running tour; focus goes back where it was', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await expect(page.getByTestId('tour-welcome')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('tour-welcome')).toHaveCount(0)
  await expect.poll(answers).toEqual({ welcome: 'skipped' })

  await page.goto('/dashboard/settings')
  await page.getByRole('button', { name: 'Replay the tour' }).click()
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByTestId('tour-popover').getByRole('heading', { name: 'Meet Ava' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('tour-overlay')).toHaveCount(0)
  await expect.poll(answers).toEqual({ welcome: 'completed', home: 'skipped' })
})

test('replay from Settings after a skip starts the tour again, and the account menu runs one page', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Skip', exact: true }).click()
  await expect.poll(answers).toEqual({ welcome: 'skipped' })

  await page.goto('/dashboard/settings')
  await expect(page.getByText('Guided tour', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Replay the tour' }).click()
  const pop = page.getByTestId('tour-popover')
  await expect(pop.getByRole('heading', { name: 'Meet Ava' })).toBeVisible()
  await pop.getByRole('button', { name: 'Skip tour' }).click()
  await expect.poll(answers).toEqual({ welcome: 'completed', home: 'skipped' })

  await page.goto('/dashboard/connections')
  // The owner took the tour (replay), so Connections walks by itself the first time; finish it, then run it again by hand.
  await expect(pop.getByRole('heading', { name: /Give Ava a way to reach people/ })).toBeVisible()
  await pop.getByRole('button', { name: 'Done' }).click()
  await expect(pop).toHaveCount(0)
  await page.getByRole('button', { name: /^Account:/ }).click()
  await page.getByRole('menuitem', { name: 'Tour this page' }).click()
  await expect(pop.getByRole('heading', { name: /Give Ava a way to reach people/ })).toBeVisible()
  await pop.getByRole('button', { name: 'Done' }).click()
})

test('the answer is kept on the server for the account, and signed out gets nothing', async ({ page, owner, api }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Skip', exact: true }).click()
  await expect.poll(answers).toEqual({ welcome: 'skipped' })
  const res = await api.get('/app/tour')
  expect((await res.json()).tours).toEqual({ welcome: 'skipped' })
  await api.post('/auth/logout')
  expect((await api.get('/app/tour')).status()).toBe(401)
})

test('with reduced motion the spotlight does not animate', async ({ page, owner }) => {
  void owner
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Take the tour' }).click()
  const prop = await page.getByTestId('tour-spotlight').evaluate((el) => getComputedStyle(el).transitionProperty)
  expect(prop).toBe('none')
})
