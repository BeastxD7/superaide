// #81: axe-core on the screens fe-a11y.spec.ts does not cover: the AI model, Capabilities and Needs-you pages, and the
// introduction (each of its steps and the three "About you" tabs).
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { openTab } from '../../src/helpers/intro-sources'

async function violations(page: Page) {
  await page.waitForTimeout(700) // let entrance animations finish: axe would read a half-faded colour
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 4).join(' | ')}`)
}

test('the AI model, Capabilities and Needs-you pages have no axe violations', async ({ page, owner }) => {
  void owner
  for (const path of ['/dashboard/models', '/dashboard/capabilities', '/dashboard/needs-you']) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
    expect(await violations(page), path).toEqual([])
  }
})

test.describe('the introduction', () => {
  test.use({ ownerState: 'hired' })

  test('its first step has no axe violations', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    await expect(page.getByRole('button', { name: 'Nice to meet you' })).toBeVisible()
    expect(await violations(page)).toEqual([])
  })

  test('the About you tabs (chat, files and links, call) have no axe violations', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    await page.getByRole('button', { name: 'Nice to meet you' }).click()
    await expect(page.getByRole('tablist', { name: 'How to introduce yourself' })).toBeVisible()
    for (const tab of ['Tell me here', 'Files & links', 'Video call'] as const) {
      await openTab(page, tab)
      expect(await violations(page), tab).toEqual([])
    }
  })
})
