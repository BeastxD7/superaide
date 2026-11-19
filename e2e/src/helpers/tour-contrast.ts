// Contrast helpers for the guided-tour specs (#657). WCAG relative luminance over the opaque card behind the text.
import type { Locator, Page } from '@playwright/test'
import { expect } from '../fixtures'

// #657: the headings inherited a global dark `text-ink`, so in dark mode they were dark on a dark card.
function luminance([r, g, b]: number[]): number {
  const f = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
// Computed colours can be oklab()/color-mix() with alpha, so let the browser resolve them to sRGB through a canvas.
async function rgbaOf(el: Locator, prop: 'color' | 'backgroundColor'): Promise<number[]> {
  return el.evaluate((e, p) => {
    const c = document.createElement('canvas')
    c.width = c.height = 1
    const ctx = c.getContext('2d', { willReadFrequently: true })!
    ctx.fillStyle = getComputedStyle(e)[p]
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
    return [r, g, b, a / 255]
  }, prop)
}
// Contrast of an element's text over the opaque card behind it (alpha text is blended onto the card).
async function contrast(card: Locator, el: Locator) {
  const own = await rgbaOf(el, 'backgroundColor')
  const bg = own[3] > 0.99 ? own : await rgbaOf(card, 'backgroundColor')
  const fg = await rgbaOf(el, 'color')
  const mix = fg.slice(0, 3).map((c, i) => c * fg[3] + bg[i] * (1 - fg[3]))
  const [a, b] = [luminance(mix), luminance(bg)]
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

export async function expectTourContrast(page: Page, scheme: 'dark' | 'light'): Promise<void> {
  await page.emulateMedia({ colorScheme: scheme })
  await page.goto('/dashboard')
  const welcomeCard = page.getByRole('dialog', { name: 'Welcome. Want a quick tour?' })
  await expect(welcomeCard).toBeVisible()
  const wh = welcomeCard.getByRole('heading')
  if (scheme === 'dark') expect(await wh.evaluate((e) => getComputedStyle(e).color)).toBe('rgb(255, 255, 255)')
  for (const el of [wh, welcomeCard.locator('p'), welcomeCard.getByRole('button', { name: 'Skip', exact: true }), welcomeCard.getByRole('button', { name: 'Take the tour' })]) {
    expect(await contrast(welcomeCard, el)).toBeGreaterThanOrEqual(4.5)
  }
  await welcomeCard.getByRole('button', { name: 'Take the tour' }).click()
  const pop = page.getByTestId('tour-popover')
  const heading = pop.getByRole('heading')
  await expect(heading).toBeVisible()
  if (scheme === 'dark') expect(await heading.evaluate((e) => getComputedStyle(e).color)).toBe('rgb(255, 255, 255)')
  const parts = [heading, pop.getByText(/^Step 1 of \d+$/), pop.locator('p').nth(1), pop.getByRole('button', { name: 'Skip tour' }), pop.getByRole('button', { name: 'Next' })]
  for (const el of parts) expect(await contrast(pop, el)).toBeGreaterThanOrEqual(4.5)
}
