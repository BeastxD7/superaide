// Chat streams: you watch Ava think, use her tools one by one and write the answer, and
// afterwards the reply keeps a folded record of what she did.
import { expect, test } from '../../src/fixtures'

function sse(events: [string, unknown][]): string {
  return ': open\n\n' + events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')
}

test('while Ava works you see her reasoning, each tool call and the answer as it comes @smoke', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  await expect(box).toBeVisible()

  // The turn is mid-way: a tool still running, the answer half written. Hold the history
  // reload that ends the turn, so the live view stays on screen to check.
  await page.route('**/api/app/chat/stream', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sse([
        ['status', { text: 'Thinking' }],
        ['reasoning', { delta: 'Check the notes before answering.' }],
        ['narration', { text: 'Let me look that up.' }],
        ['tool', { id: 't1', name: 'recall', state: 'running', label: 'Searching your notes and messages for “gym”', detail: '' }],
        ['tool', { id: 't1', name: 'recall', state: 'done', label: 'Searching your notes and messages for “gym”', detail: 'Found 2 sources' }],
        ['tool', { id: 't2', name: 'create_reminder', state: 'running', label: 'Setting a reminder: Gym, 7pm', detail: '' }],
        ['text', { delta: 'Your gym is ' }],
        ['text', { delta: 'Cult Fit.' }],
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

  await box.fill('Where is my gym? Remind me at 7pm.')
  await box.press('Enter')

  const live = page.getByTestId('live-reply')
  await expect(live).toBeVisible()
  await expect(live.getByText('Check the notes before answering.')).toBeVisible()
  await expect(live.getByText('Let me look that up.')).toBeVisible()
  const steps = live.getByRole('list', { name: 'What Ava did' })
  await expect(steps.locator('[data-tool="recall"]')).toHaveAttribute('data-state', 'done')
  await expect(steps.getByText('Found 2 sources')).toBeVisible()
  await expect(steps.locator('[data-tool="create_reminder"]')).toHaveAttribute('data-state', 'running')
  await expect(live.getByText('Your gym is Cult Fit.')).toBeVisible()
  // The narration isn't part of the answer.
  await expect(live.getByText('Let me look that up.Your gym')).toHaveCount(0)

  release()
  await expect(live).toHaveCount(0)
})

test('a real turn streams through the app and the reply keeps a record of the work @smoke', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/chat')
  const box = page.getByRole('textbox', { name: 'Message Ava' })
  const stream = page.waitForResponse('**/api/app/chat/stream')
  await box.fill('Hello Ava, how are you today?')
  await box.press('Enter')
  const res = await stream
  expect(res.headers()['content-type']).toContain('text/event-stream')
  const body = await res.text()
  expect(body).toContain('event: status')
  expect(body).toContain('event: done')
  // Once done, the saved reply replaces the live view, and the box is ready again.
  await expect(page.getByTestId('live-reply')).toHaveCount(0)
  await expect(page.getByText('Hello Ava, how are you today?', { exact: true })).toBeVisible()
  await expect(box).toBeEnabled()
})

test('a reply that used tools can be opened to see how it was worked out', async ({ page, owner }) => {
  void owner
  await page.route('**/api/app/messages', async (route) => {
    const res = await route.fetch()
    const messages = (await res.json()) as Record<string, unknown>[]
    messages.push({
      id: 'e2e-worked',
      from: 'agent',
      text: 'Reminder set for 7pm.',
      at: new Date().toISOString(),
      kind: 'chat',
      sources: [],
      reasoning: 'They asked for 7pm today.',
      activity: [
        { kind: 'say', label: 'Setting that now.' },
        { kind: 'tool', name: 'create_reminder', state: 'done', label: 'Setting a reminder: Gym, 7pm', detail: 'Reminder set: Gym, today at 19:00.' },
      ],
    })
    await route.fulfill({ response: res, json: messages })
  })
  await page.goto('/dashboard/chat')
  await expect(page.getByText('Reminder set for 7pm.')).toBeVisible()
  const log = page.getByTestId('work-log').last()
  await expect(log.getByText('Setting a reminder: Gym, 7pm')).toBeHidden()
  await log.getByText('Worked through 1 step').click()
  await expect(log.getByText('Setting a reminder: Gym, 7pm')).toBeVisible()
  await expect(log.getByText('Reminder set: Gym, today at 19:00.')).toBeVisible()
  await expect(log.getByText('They asked for 7pm today.')).toBeHidden()
  await log.getByText('Reasoning').click()
  await expect(log.getByText('They asked for 7pm today.')).toBeVisible()
})
