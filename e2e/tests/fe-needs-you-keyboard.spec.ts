// #81: a keyboard-only run of the Needs-you list, beyond its list switcher: tab order, Enter and Space activate,
// focus does not fall to the page when the item you acted on disappears, and Escape does nothing by itself.
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { triageOn } from '../src/helpers/triage'

const make = (id: string, sender: string) => ({
  id,
  decision: 'interrupt',
  reason: `${sender} asked something that cannot wait`,
  sender,
  quotes: [],
  rule_hits: [],
  stage: 'infant',
  maybe_urgent: false,
  shadow_decision: null,
  feedback: null,
  created_at: '2026-10-22T10:00:00Z',
  escalation: null,
})

// A stateful list: acting on an item removes it, as the real server does.
async function scriptedList(page: Page) {
  let waiting = [make('k1', 'Meera Iyer'), make('k2', 'Rahul Verma'), make('k3', 'Priya Tan')]
  const verbs: string[] = []
  await triageOn(page)
  await page.route('**/api/app/needs-you', (r) =>
    r.fulfill({ json: { needs_you: waiting, handled: [], held: [], counts: { needs_you: waiting.length, handled: 0, held: 0 }, stage: 'infant' } }),
  )
  await page.route('**/api/app/needs-you/*/action', (r) => {
    const id = r.request().url().split('/').slice(-2)[0]
    verbs.push(`${id}:${(r.request().postDataJSON() as { verb: string }).verb}`)
    waiting = waiting.filter((i) => i.id !== id)
    return r.fulfill({ json: { ok: true } })
  })
  return verbs
}

const focusedName = (page: Page) =>
  page.evaluate(() => {
    const el = document.activeElement
    return el && el !== document.body ? `${el.tagName.toLowerCase()}:${(el.textContent ?? '').trim() || el.getAttribute('aria-label') || ''}` : 'body'
  })

test('Tab walks the list in reading order and Enter acts on the focused item only', async ({ page, owner }) => {
  void owner
  const verbs = await scriptedList(page)
  await page.goto('/dashboard/needs-you')
  const list = page.getByRole('list', { name: 'Needs you' })
  await expect(list.getByRole('listitem')).toHaveCount(3)

  // Switcher first, then each item's buttons in order.
  await page.getByRole('group', { name: 'Lists' }).getByRole('button', { name: /^Needs you/ }).focus()
  const seen: string[] = []
  for (let i = 0; i < 7; i++) {
    await page.keyboard.press('Tab')
    seen.push(await focusedName(page))
  }
  const order = seen.filter((s) => /Seen it|Not urgent/.test(s))
  expect(order.slice(0, 6)).toEqual(['button:Seen it, thanks', 'button:Not urgent', 'button:Seen it, thanks', 'button:Not urgent', 'button:Seen it, thanks', 'button:Not urgent'].slice(0, order.length))
  expect(order.length).toBeGreaterThanOrEqual(4)

  const second = list.getByRole('listitem').nth(1)
  await second.getByRole('button', { name: 'Seen it, thanks' }).focus()
  await page.keyboard.press('Enter')
  await expect(list.getByRole('listitem')).toHaveCount(2)
  expect(verbs).toEqual(['k2:done'])
  await expect(list).not.toContainText('Rahul Verma')
})

test('Space also activates a button, and focus lands inside the list when the item goes away', async ({ page, owner }) => {
  void owner
  const verbs = await scriptedList(page)
  await page.goto('/dashboard/needs-you')
  const list = page.getByRole('list', { name: 'Needs you' })
  await expect(list.getByRole('listitem')).toHaveCount(3)

  await list.getByRole('listitem').first().getByRole('button', { name: 'Not urgent' }).focus()
  await page.keyboard.press('Space')
  await expect(list.getByRole('listitem')).toHaveCount(2)
  expect(verbs).toEqual(['k1:not_urgent'])
  // The button that was focused is gone: focus must not fall back to the top of the page.
  await expect.poll(() => focusedName(page), { message: 'focus after the item disappeared' }).not.toBe('body')
  await expect(list.locator(':focus')).toHaveCount(1)

  // From there the keyboard user carries on with the next item without reaching for the mouse.
  await page.keyboard.press('Enter')
  await expect(list.getByRole('listitem')).toHaveCount(1)
  expect(verbs).toHaveLength(2)
})

test('Escape does not act on, dismiss or move anything in the list', async ({ page, owner }) => {
  void owner
  const verbs = await scriptedList(page)
  await page.goto('/dashboard/needs-you')
  const list = page.getByRole('list', { name: 'Needs you' })
  await expect(list.getByRole('listitem')).toHaveCount(3)
  const button = list.getByRole('listitem').first().getByRole('button', { name: 'Seen it, thanks' })
  await button.focus()
  await page.keyboard.press('Escape')
  await expect(button).toBeFocused()
  await expect(list.getByRole('listitem')).toHaveCount(3)
  expect(verbs).toEqual([])
})
