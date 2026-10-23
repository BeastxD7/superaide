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

  test('stat tiles and cards in a row have equal width and height, at both widths', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/chat', { text: 'Console design check' }))
    for (const [width, height] of [[1440, 900], [390, 844]]) {
      await page.setViewportSize({ width, height })
      await openConsole(page)
      for (const tab of ['Overview', 'Spend']) {
        await goTo(page, tab)
        // Tiles inside one stat row: same width and same height (within a pixel), whatever the screen.
        for (const group of await page.locator('.c-stats').all()) {
          const boxes = await group.locator('.c-stat').evaluateAll((els) => els.map((e) => ({ w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height })))
          expect(boxes.length, `${tab}: stat tiles exist`).toBeGreaterThan(1)
          for (const b of boxes) {
            expect(Math.abs(b.w - boxes[0].w), `${tab} @${width}: equal tile widths`).toBeLessThanOrEqual(1)
            expect(Math.abs(b.h - boxes[0].h), `${tab} @${width}: equal tile heights`).toBeLessThanOrEqual(1)
          }
        }
        // Cards and tiles laid out by Grid: equal widths in each row, and no row holds one tile alone unless it is the only one.
        for (const grid of await page.locator('.c-grid').all()) {
          const kids = await grid.evaluateAll((els) => {
            const items = Array.from(els[0].children).map((c) => c.getBoundingClientRect())
            return items.map((r) => ({ w: r.width, top: Math.round(r.top) }))
          })
          for (const k of kids) expect(Math.abs(k.w - kids[0].w), `${tab} @${width}: equal widths in a grid`).toBeLessThanOrEqual(1)
          const rows = new Map<number, number>()
          for (const k of kids) rows.set(k.top, (rows.get(k.top) ?? 0) + 1)
          if (kids.length > 1) expect(new Set(rows.values()).size, `${tab} @${width}: every grid row is full`).toBe(1)
        }
      }
    }
  })

  test('Overview and Spend have no axe violations on a phone, and the growth chart scrolls by keyboard', async ({ page, api, owner }) => {
    void owner
    void api
    await page.setViewportSize({ width: 390, height: 844 })
    await openConsole(page)
    await page.waitForTimeout(700) // entrance animation
    expect(await violations(page), 'Overview @390').toEqual([])
    await expect(page.getByRole('group', { name: /Growth track/ })).toHaveAttribute('tabindex', '0')
    await goTo(page, 'Spend')
    expect(await violations(page), 'Spend @390').toEqual([])
  })

  test('the spend table has uniform rows, a sticky header inside its own scroll area, and no axe violations', async ({ page, api }) => {
    void api
    const entries = Array.from({ length: 40 }, (_, i) => ({
      id: `e${i}`,
      purpose: i % 3 ? 'chat_reply' : 'extract_facts',
      model: 'claude-haiku-4-5',
      status: i === 3 ? 'reserved' : 'settled',
      cost_usd: 0.0012 + i / 1e5,
      reserved_usd: 0.002,
      input_tokens: 1200 + i,
      output_tokens: 80,
      cache_read_tokens: 900,
      at: new Date(Date.now() - i * 60_000).toISOString(),
    }))
    await page.route('**/api/api/spend', (r) =>
      r.fulfill({ json: { summary: { cap_usd: 10, settled_usd: 0.05, reserved_usd: 0.002, remaining_usd: 9.95, calls: 40 }, entries } }),
    )
    await openConsole(page)
    await goTo(page, 'Spend')
    const table = page.getByRole('region', { name: 'Model calls, newest first' })
    await expect(table.getByRole('row')).toHaveCount(41)
    const heights = await table.locator('tbody tr').evaluateAll((rows) => rows.map((r) => Math.round(r.getBoundingClientRect().height)))
    expect(new Set(heights).size, 'every row is the same height').toBe(1)
    expect(await table.locator('th').first().evaluate((el) => getComputedStyle(el).position)).toBe('sticky')
    expect(await table.evaluate((el) => el.scrollHeight > el.clientHeight), 'long tables scroll inside their own frame').toBe(true)
    await expect(table.getByRole('row', { name: /reserved/ })).toHaveClass(/is-muted/)
    expect(await violations(page)).toEqual([])
  })

  test('People tables line up across people; Conversations is a balanced split; the log rows are uniform', async ({ page, api, owner }) => {
    void owner
    await api.ok(api.post('/app/chat', { text: 'Console design check' }))
    await new Admin(page.request).post('/simulate', { provider: 'telegram', sender_id: '5550001', sender_display: 'Shashank', chat_kind: 'dm', text: "Hi it's Shashank, new number." })
    await page.setViewportSize({ width: 1440, height: 900 })
    await openConsole(page)

    await goTo(page, 'People')
    const tables = page.getByRole('region', { name: /^Channels for / })
    expect(await tables.count(), 'one table per person').toBeGreaterThanOrEqual(2)
    const heads = await tables.evaluateAll((els) => els.map((el) => Array.from(el.querySelectorAll('th')).map((th) => Math.round(th.getBoundingClientRect().x))))
    for (const h of heads) expect(h, 'same column positions in every person card').toEqual(heads[0])
    await expect(page.getByRole('heading', { level: 2 }).first()).toBeVisible()

    await goTo(page, 'Conversations')
    const side = await page.locator('.c-split-side').boundingBox()
    const main = await page.locator('.c-split-main').boundingBox()
    expect(Math.abs(side!.height - main!.height), 'list and thread cards are equal height').toBeLessThanOrEqual(1)
    expect(main!.width / side!.width, 'one third and two thirds').toBeGreaterThan(1.8)
    expect(main!.width / side!.width).toBeLessThan(2.2)

    await goTo(page, 'Safety log')
    const heights = await page.locator('.log-row').evaluateAll((rows) => rows.map((r) => Math.round(r.getBoundingClientRect().height)))
    expect(heights.length).toBeGreaterThan(2)
    expect(Math.max(...heights) - Math.min(...heights), 'log rows keep one height on a wide screen').toBeLessThanOrEqual(1)
    await page.getByRole('group', { name: 'Filter the log' }).getByRole('button', { name: 'Refusals' }).click()
    await expect(page.getByRole('heading', { name: 'Nothing here yet.' })).toBeVisible()

    // On a phone the split stacks: the list above the thread, both full width.
    await page.setViewportSize({ width: 390, height: 844 })
    await goTo(page, 'Conversations')
    const s2 = await page.locator('.c-split-side').boundingBox()
    const m2 = await page.locator('.c-split-main').boundingBox()
    expect(m2!.y).toBeGreaterThan(s2!.y + s2!.height - 1)
    expect(Math.abs(m2!.width - s2!.width)).toBeLessThanOrEqual(1)
  })

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
