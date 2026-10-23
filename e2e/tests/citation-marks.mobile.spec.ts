// Issue #341 on a 390px phone: the source opens as a bottom sheet that fits the screen, with no sideways scroll.
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '../src/fixtures'

test.use({ viewport: { width: 390, height: 844 } })

const at = '2026-10-23T18:38:00Z'
const sources = [
  { n: 1, uri: 'ava://memory/0f6c1a2e-1111-4222-8333-444455556666', kind: 'memory', title: 'Note · preference · from the app', cited_text: 'Wants a reply sent as a new email' },
]

test('a mark opens a bottom sheet that fits 390px, closes with the button, and passes axe', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/messages', (r) =>
    r.fulfill({ json: [{ id: 'm2', from: 'agent', text: 'You prefer a new email. [1]', at, kind: 'chat', sources }] }),
  )
  await page.goto('/dashboard/chat')
  await page.getByRole('button', { name: /^Source 1:/ }).click()
  const sheet = page.getByRole('dialog', { name: /^Source 1:/ })
  await expect(sheet).toBeVisible()
  const box = await sheet.boundingBox()
  expect(box?.x).toBe(0)
  expect(box?.width).toBe(390)
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeCloseTo(844, 0) // sits on the bottom edge
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(results.violations.map((v) => v.id)).toEqual([])
  await sheet.getByRole('button', { name: 'Close source' }).click()
  await expect(sheet).toHaveCount(0)
})
