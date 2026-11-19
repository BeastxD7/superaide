// The guided tour at phone width (#626): the prompt and the popover stay on screen and reachable.
import { expect, test } from '../../src/fixtures'

test.use({ tourOn: true })

test('prompt and popover fit a phone, no sideways scroll, buttons reachable', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  const welcome = page.getByRole('dialog', { name: 'Welcome. Want a quick tour?' })
  await expect(welcome).toBeVisible()
  await expect(welcome.getByRole('button', { name: 'Take the tour' })).toBeInViewport()
  await expect(welcome.getByRole('button', { name: 'Skip', exact: true })).toBeInViewport()
  await welcome.getByRole('button', { name: 'Take the tour' }).click()
  const pop = page.getByTestId('tour-popover')
  await expect(pop).toBeVisible()
  const vw = page.viewportSize()!.width
  for (let i = 0; i < 6; i++) {
    const box = (await pop.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(vw)
    await expect(pop.getByRole('button', { name: /Next|Done/ })).toBeInViewport()
    const done = await pop.getByRole('button', { name: 'Done' }).count()
    if (done) break
    await pop.getByRole('button', { name: 'Next' }).click()
  }
  await pop.getByRole('button', { name: 'Done' }).click()
  const scrolls = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(scrolls).toBe(false)
})
