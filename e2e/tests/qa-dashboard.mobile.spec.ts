// QA bug hunt: the dashboard on a phone (Pixel 7). The tab bar is a build-time flag that is off in
// this build, so these cover the sidebar sheet and every page's layout.
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { expectNoSidewaysScroll, goToDashboardPage, sidebarTrigger } from '../src/helpers/workspace'

const QA = `${process.env.QA_SHOTS_DIR ?? 'test-results/qa-shots'}/dashboard`

const PAGES: { path: string; heading: RegExp | string }[] = [
  { path: '/dashboard', heading: /^Good (morning|afternoon|evening), Ananya$/ },
  { path: '/dashboard/chat', heading: 'Your conversation with Ava' },
  { path: '/dashboard/notebook', heading: "Ava's notebook" },
  { path: '/dashboard/people', heading: 'People who matter' },
  { path: '/dashboard/capabilities', heading: 'Capabilities' },
  { path: '/dashboard/connections', heading: 'Connections' },
  { path: '/dashboard/models', heading: /./ },
  { path: '/dashboard/reminders', heading: 'Reminders' },
  { path: '/dashboard/settings', heading: 'Settings' },
]

// Visible things that stick out past the right edge of the screen (inner scroll areas hide this
// from documentElement.scrollWidth).
async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth
    const out: string[] = []
    for (const el of Array.from(document.querySelectorAll('main *'))) {
      const r = el.getBoundingClientRect()
      if (!r.width || !r.height) continue
      // Inside a sideways-scrolling row (the quick actions) is fine.
      let p: HTMLElement | null = el.parentElement
      let scrolls = false
      while (p) {
        const o = getComputedStyle(p).overflowX
        if (o === 'auto' || o === 'scroll' || o === 'hidden') {
          if (p.getBoundingClientRect().right <= width + 1) {
            scrolls = true
            break
          }
        }
        p = p.parentElement
      }
      if (!scrolls && r.right > width + 1) out.push(`${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 40)}" right=${Math.round(r.right)}`)
    }
    return out
  })
}

test.describe('dashboard on a phone', () => {
  for (const p of PAGES) {
    test(`${p.path} fits the screen`, async ({ page, owner }) => {
      void owner
      await page.goto(p.path)
      await expect(page.getByRole('heading', { level: 1, name: p.heading })).toBeVisible()
      await expect(page.locator('main [aria-busy="true"]')).toHaveCount(0)
      await expectNoSidewaysScroll(page)
      expect(await overflowing(page)).toEqual([])
    })
  }

  test('the sidebar sheet moves between pages and closes after each', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    for (const name of ['Notebook', 'People', 'Capabilities', 'Connections', 'Settings', 'Home']) {
      await goToDashboardPage(page, name)
      await expect(page.getByRole('main').locator('header')).toContainText(name)
      await expect(page.getByRole('dialog')).toHaveCount(0)
    }
    await sidebarTrigger(page).click()
    await page.screenshot({ path: `${QA}/mobile-sheet.png` })
  })
})
