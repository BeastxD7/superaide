// Accessibility (WCAG 2.2 AA) with axe-core on the main screens, plus keyboard and focus checks (issue #78).
// Colour contrast is part of the axe run, so a token that slips below 4.5:1 fails here.
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'
import { triageOn } from '../../src/helpers/triage'

const at = '2026-10-22T10:00:00Z'

async function violations(page: Page) {
  await page.waitForTimeout(700) // let entrance animations finish: axe would read a half-faded colour
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  return results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 4).join(' | ')}`)
}

test('signed-out screens have no axe violations', async ({ page }) => {
  for (const path of ['/login', '/signup', '/forgot-password']) {
    await page.goto(path)
    await expect(page.getByRole('heading').first()).toBeVisible()
    expect(await violations(page), path).toEqual([])
  }
})

test('the chat, with a message each way and a reply being written, has no axe violations', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/messages', (r) =>
    r.fulfill({
      json: [
        { id: 'm1', from: 'you', text: 'Where is my gym?', at },
        { id: 'm2', from: 'agent', text: 'Your gym is Cult Fit.', at, kind: 'chat' },
      ],
    }),
  )
  await page.goto('/dashboard/chat')
  await expect(page.getByText('Your gym is Cult Fit.', { exact: true })).toBeVisible()
  expect(await violations(page)).toEqual([])
})

test('dashboard pages have no axe violations', async ({ page, owner }) => {
  void owner
  for (const path of ['/dashboard', '/dashboard/notebook', '/dashboard/people', '/dashboard/settings', '/dashboard/reminders', '/dashboard/connections']) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible()
    expect(await violations(page), path).toEqual([])
  }
})

test('the Needs-you list switcher is a group of toggle buttons that work from the keyboard', async ({ page, owner }) => {
  void owner
  await triageOn(page)
  await page.route('**/api/app/needs-you', (r) =>
    r.fulfill({ json: { needs_you: [], handled: [], held: [], counts: { needs_you: 0, handled: 0, held: 0 }, stage: 'infant' } }),
  )
  await page.goto('/dashboard/needs-you')
  const group = page.getByRole('group', { name: 'Lists' })
  await expect(group.getByRole('tab')).toHaveCount(0) // no half-built tab pattern
  const handled = group.getByRole('button', { name: /Handled for you/ })
  await expect(group.getByRole('button', { name: /^Needs you/ })).toHaveAttribute('aria-pressed', 'true')
  await handled.focus()
  await page.keyboard.press('Enter')
  await expect(handled).toHaveAttribute('aria-pressed', 'true')
})

test('Forget moves focus to Keep, and Escape on the account menu returns focus to its button', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/notebook*', (r) =>
    r.fulfill({ json: { memories: [{ id: 'n1', text: 'Likes tea', category: 'preference', source: 'chat', document: null, shareable: false, private: false, at }] } }),
  )
  await page.goto('/dashboard/notebook')
  await page.getByRole('button', { name: 'Forget this' }).click()
  await expect(page.getByRole('button', { name: 'Keep' })).toBeFocused()

  await page.goto('/dashboard')
  const account = page.getByRole('button', { name: /^Account: signed in as/ })
  await account.click()
  await page.keyboard.press('Escape')
  await expect(account).toBeFocused()
})
