// Chat and streaming edge cases (issues #76 and #74): a stream that ends without "done", the IME
// Enter, the screen-reader live region, the Needs-you inbox after a failed first call, and the
// console's conversation switch race. Model replies and network faults are scripted with page.route.
import type { Page } from '@playwright/test'
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'
import { triageOn } from '../../src/helpers/triage'

function sse(events: [string, unknown][]): string {
  return ': open\n\n' + events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')
}

const at = '2026-10-22T10:00:00Z'

test('a stream that ends without done shows the lost-connection note, then the saved reply when it lands', async ({ page, owner }) => {
  void owner
  let phase: 'empty' | 'asked' | 'answered' = 'empty'
  await page.route('**/api/app/messages', (route) => {
    const body =
      phase === 'empty'
        ? []
        : phase === 'asked'
          ? [{ id: 'm1', from: 'you', text: 'Where is my gym?', at }]
          : [
              { id: 'm1', from: 'you', text: 'Where is my gym?', at },
              { id: 'm2', from: 'agent', text: 'Your gym is Cult Fit.', at, kind: 'chat' },
            ]
    return route.fulfill({ json: body })
  })
  await page.route('**/api/app/chat/stream', (route) => {
    phase = 'asked'
    // Opens, says something, then closes: no done or error event (a proxy idle timeout).
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([['status', { text: 'Thinking' }]]) })
  })
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  await box.fill('Where is my gym?')
  await box.press('Enter')
  await expect(page.getByText('Lost the live connection')).toBeVisible()
  // The reply is written while the owner waits: the page checks again by itself.
  phase = 'answered'
  await expect(page.getByText('Your gym is Cult Fit.', { exact: true })).toBeVisible({ timeout: 15_000 })
})

test('leaving the chat mid-reply and coming back shows the reply once it is saved', async ({ page, owner }) => {
  void owner
  let answered = false
  await page.route('**/api/app/messages', (route) =>
    route.fulfill({
      json: [
        { id: 'm1', from: 'you', text: 'Search the web for gyms', at },
        ...(answered ? [{ id: 'm2', from: 'agent', text: 'Found three gyms.', at, kind: 'chat' }] : []),
      ],
    }),
  )
  await page.goto('/dashboard/chat')
  await expect(page.getByText('Search the web for gyms')).toBeVisible()
  await expect(page.getByText('Found three gyms.', { exact: true })).toHaveCount(0)
  answered = true
  await expect(page.getByText('Found three gyms.', { exact: true })).toBeVisible({ timeout: 15_000 })
})

test('Enter that confirms an IME candidate is not a send; a plain Enter is', async ({ page, owner }) => {
  void owner
  let sent = 0
  await page.route('**/api/app/chat/stream', (route) => {
    sent += 1
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: sse([['done', { mode: 'chat' }]]) })
  })
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  await box.fill('こんにちは')
  await box.evaluate((el) => {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, isComposing: true }))
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229, bubbles: true, cancelable: true }))
  })
  await page.waitForTimeout(500)
  expect(sent).toBe(0)
  await expect(box).toHaveValue('こんにちは')
  await box.press('Enter')
  await expect.poll(() => sent).toBe(1)
})

test('while a reply streams the transcript is not a live region; one hidden status says she is working', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/chat/stream', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sse([
        ['status', { text: 'Thinking' }],
        ['tool', { id: 't1', name: 'recall', state: 'running', label: 'Searching', detail: '' }],
        ['text', { delta: 'Half an answer' }],
        ['done', { mode: 'chat' }],
      ]),
    }),
  )
  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => (release = resolve))
  await page.route('**/api/app/messages', async (route) => {
    await held
    await route.continue()
  })
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  await box.fill('Hello')
  await box.press('Enter')
  await expect(page.getByTestId('live-reply')).toBeVisible()
  await expect(page.getByTestId('chat-status')).toHaveText('Ava is working')
  // Nothing inside the transcript announces on its own (no aria-live, no extra status roles).
  await expect(page.getByTestId('live-reply').locator('[aria-live], [role="status"]')).toHaveCount(0)
  await expect(page.locator('main [aria-live]')).toHaveCount(0)
  release()
  await expect(page.getByTestId('live-reply')).toHaveCount(0)
  await expect(page.getByTestId('chat-status')).toHaveText('Ava replied')
})

const NEEDS_YOU = { needs_you: [], handled: [], held: [], counts: { needs_you: 0, handled: 0, held: 0 }, stage: 'infant' }

// The sidebar, the tab bar and the page each ask once, so "the first call" is really "while it is down".
async function needsYouDown(page: Page): Promise<{ up: () => void }> {
  let down = true
  await triageOn(page)
  await page.route('**/api/app/needs-you', (route) =>
    down ? route.fulfill({ status: 502, body: 'bad gateway' }) : route.fulfill({ json: NEEDS_YOU }),
  )
  return { up: () => (down = false) }
}

test('a failed first Needs-you call does not hide the inbox for the session: polling recovers it', async ({ page, owner }) => {
  void owner
  await page.clock.install()
  const net = await needsYouDown(page)
  await page.goto('/dashboard')
  const nav = page.getByRole('link', { name: /^Needs you/ })
  await expect(page.getByRole('link', { name: 'Home' }).first()).toBeVisible()
  await expect(nav).toHaveCount(0)
  net.up()
  await page.clock.fastForward(61_000)
  await expect(nav.first()).toBeVisible()
})

test('the Needs-you page offers a retry instead of skeleton rows forever', async ({ page, owner }) => {
  void owner
  const net = await needsYouDown(page)
  await page.goto('/dashboard/needs-you')
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  net.up()
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByText('Nothing needs you right now.')).toBeVisible()
})

test('console: a slow answer for the previous conversation never lands on the selected one', async ({ page, api }) => {
  void api
  const conv = (id: string, title: string) => ({ id, provider: 'telegram', kind: 'dm', audience_class: 'single', title, messages: 1, last_at: at })
  const row = (id: string, ctx: string, content: string) => ({
    id, context_id: ctx, direction: 'in', sender: 'x', trust: 1, audience_size: 1, audience_has_unknown: false, content, at,
  })
  let aAnswered: () => void = () => {}
  const aDone = new Promise<void>((resolve) => (aAnswered = resolve))
  await page.route('**/api/conversations/page*', (route) =>
    route.fulfill({ json: { items: [conv('A', 'a'), conv('B', 'b')], next_cursor: null, has_more: false } }),
  )
  await page.route('**/api/events?*', async (route) => {
    const ctx = new URL(route.request().url()).searchParams.get('context_id')
    if (ctx === 'A') {
      await new Promise((r) => setTimeout(r, 1200))
      await route.fulfill({ json: [row('1', 'A', 'MESSAGE FROM A')] })
      return aAnswered()
    }
    return route.fulfill({ json: [row('2', 'B', 'MESSAGE FROM B')] })
  })
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'Conversations', exact: true }).click()
  const open = page.getByRole('button', { name: "Open, and record it in the owner's log" })
  if (await open.isVisible()) {
    await page.getByLabel(/^Reason/).fill('end-to-end test of the console')
    await open.click()
  }
  // A is selected first (its answer is slow); switch to B once A's request is out, before it arrives.
  await page.waitForRequest((r) => r.url().includes('context_id=A'))
  await page.getByRole('navigation', { name: 'Conversations' }).locator('button').nth(1).click()
  await expect(page.getByText('MESSAGE FROM B')).toBeVisible()
  await aDone // A's late answer has now arrived (the 3 s poll is not due yet)
  await page.waitForTimeout(300)
  // A one-shot count, not a retrying assertion: the next poll would hide the bug.
  expect(await page.getByText('MESSAGE FROM A').count()).toBe(0)
  expect(await page.getByText('MESSAGE FROM B').count()).toBe(1)
})
