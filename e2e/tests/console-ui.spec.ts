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
  // On a phone the sidebar is a drawer: open it from the trigger, pick the page (which closes it).
  const phone = !(await nav(page).isVisible())
  if (phone) {
    await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
    await expect(nav(page)).toBeVisible()
  }
  await nav(page).getByRole('button', { name, exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (opts.unlock !== false && (await open.isVisible().catch(() => false))) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of the console design')
    await open.click()
  }
  if (phone) await expect(nav(page)).toBeHidden()
  else await expect(nav(page).getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('navigation', { name: 'breadcrumb' }).getByText(name, { exact: true })).toBeVisible()
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
    test(`${name}: no sideways scroll on any tab, one h1 per page, content centred in the page area`, async ({ page, api, owner }) => {
      void owner
      await api.ok(api.post('/app/chat', { text: 'Console design check' }))
      await page.setViewportSize({ width, height })
      await openConsole(page)
      for (const tab of TABS) {
        await goTo(page, tab)
        expect(await overflow(page), `${tab} scrolls sideways at ${width}px`).toBeLessThanOrEqual(0)
        await expect(page.getByRole('heading', { level: 1 }), `${tab} has exactly one h1`).toHaveCount(1)
        const inset = (await page.locator('main.c-main').boundingBox())!
        const content = (await page.locator('main.c-main > .c-page').boundingBox())!
        // Equal space left and right of the content inside the page area.
        expect(Math.abs(content.x - inset.x - (inset.x + inset.width - (content.x + content.width))), `${tab}: content is centred`).toBeLessThanOrEqual(1)
        expect(content.x + content.width, `${tab}: content stays inside the window`).toBeLessThanOrEqual(width)
      }
    })
  }

  test('the sidebar is reachable from the keyboard in reading order, with a focus ring', async ({ page, api }) => {
    void api
    await page.setViewportSize({ width: 1440, height: 900 })
    await openConsole(page)
    await nav(page).getByRole('button', { name: 'Overview' }).focus()
    const order: string[] = []
    for (let i = 0; i < 9; i++) {
      order.push((await page.evaluate(() => (document.activeElement as HTMLElement | null)?.innerText?.trim() || document.activeElement?.getAttribute('aria-label') || '')) || '')
      await page.keyboard.press('Tab')
    }
    expect(order).toEqual(['Overview', 'Spend', 'People', 'Conversations', 'Safety log', 'Try an impostor', 'Disconnect', 'Toggle Sidebar', 'Pause Ava'])
    await nav(page).getByRole('button', { name: 'People' }).focus()
    // The shared sidebar draws its focus ring as a 2px box-shadow ring (outline stays off).
    const ring = await nav(page).getByRole('button', { name: 'People' }).evaluate((el) => {
      const c = getComputedStyle(el)
      return c.outlineStyle !== 'none' || c.boxShadow !== 'none'
    })
    expect(ring, 'a visible focus ring').toBe(true)
  })

  test('the sidebar collapses to icons with the b shortcut, keeps every name, and has no axe violations', async ({ page, api }) => {
    void api
    await page.setViewportSize({ width: 1440, height: 900 })
    await openConsole(page)
    const sidebar = page.locator('[data-slot="sidebar"]').first()
    await expect(sidebar).toHaveAttribute('data-state', 'expanded')
    await page.keyboard.press('Control+b')
    await expect(sidebar).toHaveAttribute('data-state', 'collapsed')
    for (const tab of TABS) await expect(nav(page).getByRole('button', { name: tab, exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Disconnect' })).toBeVisible()
    await page.waitForTimeout(500)
    expect(await violations(page), 'collapsed').toEqual([])
    expect(await overflow(page)).toBeLessThanOrEqual(0)
    await page.keyboard.press('Control+b')
    await expect(sidebar).toHaveAttribute('data-state', 'expanded')
  })

  test('on a phone the sidebar is a drawer: closed at first, Escape closes it, choosing a page closes it, no axe violations open', async ({ page, api, owner }) => {
    void api
    void owner
    await page.setViewportSize({ width: 390, height: 844 })
    await openConsole(page)
    await expect(nav(page)).toBeHidden()
    const trigger = page.getByRole('button', { name: 'Toggle Sidebar' })
    await trigger.click()
    await expect(nav(page)).toBeVisible()
    await page.waitForTimeout(500)
    expect(await violations(page), 'drawer open').toEqual([])
    const drawer = await page.getByRole('dialog').boundingBox()
    expect(drawer!.x + drawer!.width, 'the drawer fits the window').toBeLessThanOrEqual(390)
    await page.keyboard.press('Escape')
    await expect(nav(page)).toBeHidden()
    await expect(trigger).toBeFocused()
    await goTo(page, 'Spend')
    await expect(page.getByRole('heading', { level: 1, name: 'Spend' })).toBeVisible()
    // The pause switch stays one press away without opening the drawer.
    await expect(page.getByRole('button', { name: 'Pause Ava' })).toBeVisible()
  })
})
