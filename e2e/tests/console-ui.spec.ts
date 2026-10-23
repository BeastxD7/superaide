// The operator console's design system (docs/design/console-ui.md): accessibility on every tab, one shared grid,
// no sideways scroll on a phone or a wide screen, one h1 per page, and a sensible keyboard order.
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { ADMIN_TOKEN } from '../src/env'
import { Admin } from '../src/helpers/workspace'

const TABS = ['Overview', 'People', 'Conversations', 'Safety log', 'Spend', 'Try an impostor']
const nav = (page: Page) => page.getByRole('navigation', { name: 'Sections' })

async function openConsole(page: Page): Promise<void> {
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await expect(page.getByRole('heading', { level: 1, name: /Ava is at the Infant stage/ })).toBeVisible()
}

// People, Conversations, the Safety log and the simulator ask for a reason first (break-glass, ADR 0009).
async function goTo(page: Page, name: string, opts: { unlock?: boolean } = {}): Promise<void> {
  await nav(page).getByRole('button', { name, exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (opts.unlock !== false && (await open.isVisible().catch(() => false))) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of the console design')
    await open.click()
  }
  await expect(nav(page).getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page')
  await page.waitForTimeout(700) // the page's entrance animation: axe would read a half-faded colour
}

async function violations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 4).join(' | ')}`)
}

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

test.describe('console design: accessibility', () => {
  test('the connect screen and every tab (locked and open) have no axe violations', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/chat', { text: 'Console design check' }))
    await page.goto('/console')
    await expect(page.getByRole('heading', { name: 'Connect to your agent' })).toBeVisible()
    await page.waitForTimeout(300)
    expect(await violations(page), 'connect').toEqual([])

    await openConsole(page)
    for (const tab of TABS) {
      await goTo(page, tab, { unlock: false })
      expect(await violations(page), `${tab} (locked)`).toEqual([])
    }
    for (const tab of TABS) {
      await goTo(page, tab)
      expect(await violations(page), `${tab} (open)`).toEqual([])
    }
  })

  test('the paused state (banner, badge, resume button) has no axe violations', async ({ page, api }) => {
    void api
    await openConsole(page)
    await new Admin(page.request).pause(true)
    await page.reload()
    await expect(page.getByText('Paused: no model calls, no replies')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Resume Ava' })).toBeVisible()
    await page.waitForTimeout(700)
    expect(await violations(page)).toEqual([])
    await new Admin(page.request).pause(false)
  })
})

test.describe('console design: one grid, every width', () => {
  for (const [name, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`${name}: no sideways scroll on any tab, one h1 per page, bar and content share edges`, async ({ page, api, owner }) => {
      void owner
      await api.ok(api.post('/app/chat', { text: 'Console design check' }))
      await page.setViewportSize({ width, height })
      await openConsole(page)
      for (const tab of TABS) {
        await goTo(page, tab)
        expect(await overflow(page), `${tab} scrolls sideways at ${width}px`).toBeLessThanOrEqual(0)
        await expect(page.getByRole('heading', { level: 1 }), `${tab} has exactly one h1`).toHaveCount(1)
        const bar = (await page.locator('.c-bar').boundingBox())!
        const main = (await page.locator('main.c-main > .c-page').boundingBox())!
        expect(Math.abs(bar.x - main.x), `${tab}: bar and content start at the same edge`).toBeLessThanOrEqual(1)
        expect(Math.abs(bar.x + bar.width - (main.x + main.width)), `${tab}: and end at the same edge`).toBeLessThanOrEqual(1)
        // Centred: equal space left and right of the content.
        expect(Math.abs(main.x - (width - (main.x + main.width))), `${tab}: content is centred`).toBeLessThanOrEqual(1)
      }
    })
  }

  test('the section tabs are reachable from the keyboard, in reading order, and show a focus ring', async ({ page, api }) => {
    void api
    await openConsole(page)
    await nav(page).getByRole('button', { name: 'Overview' }).focus()
    const order: string[] = []
    for (let i = 0; i < 9; i++) {
      order.push((await page.evaluate(() => document.activeElement?.textContent?.trim() ?? '')) || '')
      await page.keyboard.press('Tab')
    }
    expect(order.slice(0, 9)).toEqual(['Overview', 'People', 'Conversations', 'Safety log', 'Spend', 'Try an impostor', 'Pause Ava', 'Disconnect', expect.any(String)])
    await nav(page).getByRole('button', { name: 'People' }).focus()
    const ring = await nav(page).getByRole('button', { name: 'People' }).evaluate((el) => getComputedStyle(el).outlineStyle)
    expect(ring).not.toBe('none')
  })
})
