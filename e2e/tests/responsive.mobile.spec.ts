// Phone and tablet layout of the shared navbar, buttons and container (and, as later changes land, every page):
// nothing scrolls sideways, nothing sticks out, controls are comfortable to tap, text is readable, and the
// primary action sits inside the screen at 320px.
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { measure } from '../src/helpers/responsive'

const WIDTHS = [320, 360, 390, 768]
const SIGNED_OUT = ['/', '/agents/ava']

// Entrance animations fade things in; axe would read a half-faded colour as low contrast.
async function settled(page: Page) {
  await page.waitForTimeout(1500) // the longest entrance animation on these pages
}

async function expectFits(page: Page, where: string) {
  const m = await measure(page)
  expect(m.scrollWidth, `${where}: page scrolls sideways`).toBeLessThanOrEqual(m.vw)
  expect(m.wide, `${where}: elements wider than the screen`).toEqual([])
  expect(m.smallInputs, `${where}: inputs under 16px (iOS zooms in)`).toEqual([])
}

// The navbar: one 56px row, every control at least 44px tall, text 14px or more, no wrapped labels, all inside the screen.
async function expectNavbarFits(page: Page, where: string) {
  const nav = page.getByRole('navigation', { name: 'Site' })
  await expect(nav).toBeVisible()
  const vw = page.viewportSize()!.width
  const box = (await nav.boundingBox())!
  expect(box.height, `${where}: navbar height`).toBeLessThanOrEqual(60)
  expect(box.x + box.width, `${where}: navbar inside the screen`).toBeLessThanOrEqual(vw)
  const controls = nav.locator('a:visible, button:visible')
  const n = await controls.count()
  expect(n).toBeGreaterThan(1)
  for (let i = 0; i < n; i++) {
    const c = controls.nth(i)
    const b = (await c.boundingBox())!
    const name = (await c.getAttribute('aria-label')) ?? (await c.innerText()).trim()
    expect(b.height, `${where}: "${name}" is at least 44px tall`).toBeGreaterThanOrEqual(43.5)
    expect(b.width, `${where}: "${name}" is at least 44px wide`).toBeGreaterThanOrEqual(43.5)
    expect(b.x >= 0 && b.x + b.width <= vw, `${where}: "${name}" is inside the screen`).toBeTruthy()
    const { fontSize, lineHeight } = await c.evaluate((el) => {
      const s = getComputedStyle(el)
      return { fontSize: parseFloat(s.fontSize), lineHeight: parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.4 }
    })
    expect(fontSize, `${where}: "${name}" text size`).toBeGreaterThanOrEqual(14)
    // A label on one line is never much taller than a line of text plus its padding.
    if (b.height > lineHeight * 2 + 24) throw new Error(`${where}: "${name}" wraps (${b.height}px tall)`)
  }
}

for (const width of WIDTHS) {
  test.describe(`signed out at ${width}px`, () => {
    test.use({ viewport: { width, height: 780 } })

    for (const path of SIGNED_OUT) {
      test(`${path}: fits, navbar is tidy, no axe violations`, async ({ page, api }) => {
        void api
        await page.goto(path)
        await expect(page.getByRole('navigation', { name: 'Site' })).toBeVisible()
        await expectNavbarFits(page, path)
        await expectFits(page, path)
        await settled(page)
        const axe = await new AxeBuilder({ page }).analyze()
        expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`), path).toEqual([])
      })
    }

    test('the primary navbar button is fully inside the screen', async ({ page, api }) => {
      void api
      await page.goto('/')
      const cta = page.getByRole('navigation', { name: 'Site' }).getByRole('button', { name: 'Meet Ava' })
      await expect(cta).toBeVisible()
      const b = (await cta.boundingBox())!
      expect(b.x).toBeGreaterThanOrEqual(0)
      expect(b.x + b.width).toBeLessThanOrEqual(width)
    })

    test('the menu opens as a sheet that fits and reaches every link', async ({ page, api }) => {
      void api
      test.skip(width >= 768, 'the menu is for phones')
      await page.goto('/')
      await page.getByRole('button', { name: 'Menu' }).tap()
      const sheet = page.getByRole('dialog')
      await expect(sheet.getByRole('link', { name: 'Log in' })).toBeVisible()
      await expect(sheet.getByRole('link', { name: 'Safety' })).toBeVisible()
      // It slides in: wait until it stops.
      await expect.poll(async () => { const b = (await sheet.boundingBox())!; return b.x + b.width }).toBeLessThanOrEqual(width + 1)
      const b = (await sheet.boundingBox())!
      expect(b.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)
      await page.keyboard.press('Escape')
      await expect(sheet).toBeHidden()
    })
  })

  test.describe(`signed in at ${width}px`, () => {
    test.use({ viewport: { width, height: 780 } })

    test('the navbar says Dashboard on a phone, Open dashboard keeps its accessible name', async ({ page, owner }) => {
      void owner
      await page.goto('/')
      await expectNavbarFits(page, 'signed-in home')
      const cta = page.getByRole('navigation', { name: 'Site' }).getByRole('button', { name: 'Open dashboard' })
      await expect(cta).toBeVisible()
      if (width < 640) await expect(cta).toHaveText('Dashboard', { useInnerText: true })
      else await expect(cta).toHaveText('Open dashboard', { useInnerText: true })
      const b = (await cta.boundingBox())!
      expect(b.x + b.width).toBeLessThanOrEqual(width)
      await expectFits(page, 'signed-in home')
    })
  })
}
