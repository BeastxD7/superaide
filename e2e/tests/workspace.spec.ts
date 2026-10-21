// The signed-in dashboard (formerly the single /workspace screen): chatting with Ava
// (/dashboard/chat), the notebook (/dashboard/notebook), privacy between accounts, and
// Connections (/dashboard/connections: Claude, Telegram, Slack, WhatsApp).
import { createCipheriv, randomBytes } from 'node:crypto'
import { expect, query, test } from '../src/fixtures'
import { apiEnv } from '../src/env'
import {
  auditActions,
  composer,
  eventsWithText,
  expectNoSidewaysScroll,
  goToDashboardPage,
  logOutFromSidebar,
  memories,
  newVisitor,
  notebookFact,
  INTRO_LINE,
  OFFLINE_REPLY,
  openChat,
  openConnections,
  openNotebookPage,
  recordOutsideCalls,
  sendButton,
  storedSecrets,
  webConversation,
} from '../src/helpers/workspace'

const greeting = (name: string) => `Hello, ${name.split(' ')[0]}. What shall we work on?`

test.describe('workspace: chat', () => {
  test('greets the owner by first name, with an empty composer and quick actions', async ({ page, owner }) => {
    await openChat(page)
    await expect(page.getByRole('heading', { level: 1, name: greeting(owner.name) })).toBeVisible()
    await expect(composer(page)).toBeVisible()
    await expect(composer(page)).toHaveValue('')
    await expect(sendButton(page)).toBeDisabled()
    for (const label of ['How would you handle…', 'Draft a reply', 'Remember something', 'What do you know about me?']) {
      await expect(page.getByRole('button', { name: label })).toBeVisible()
    }
    await expect(page.getByRole('main').getByText('Ava: offline mode')).toBeVisible()
    // The introduction's conversation is already there.
    await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
  })

  test('a message gets a reply, and both survive a reload and a new tab', async ({ page, owner }) => {
    void owner
    const text = 'Can you keep Thursday afternoon free for the board prep?'
    await openChat(page)
    await composer(page).fill(text)
    await composer(page).press('Enter')

    await expect(page.getByText(text, { exact: true })).toBeVisible()
    await expect(page.getByText(`You said: "${text}"`)).toBeVisible()
    await expect(composer(page)).toHaveValue('')
    await expect(page.getByLabel('Ava is typing')).toHaveCount(0)

    // Stored once each way.
    const stored = await webConversation()
    const i = stored.findIndex((e) => e.direction === 'in' && e.content === text)
    expect(i, 'the message was stored').toBeGreaterThanOrEqual(0)
    expect(stored[i + 1]?.direction).toBe('out')
    expect(stored[i + 1]?.content).toMatch(OFFLINE_REPLY)
    expect(await eventsWithText(text)).toHaveLength(1)

    await page.reload()
    await expect(page.getByText(text, { exact: true })).toBeVisible()
    await expect(page.getByText(`You said: "${text}"`)).toBeVisible()

    const tab = await page.context().newPage()
    await tab.goto('/dashboard/chat')
    await expect(tab.getByText(text, { exact: true })).toBeVisible()
    await expect(tab.getByText(`You said: "${text}"`)).toBeVisible()
    await tab.close()
  })

  test('with a long conversation, the top bar and the composer stay on screen', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(page.getByRole('main').getByRole('button', { name: 'Toggle Sidebar' })).toBeInViewport()
    await expect(composer(page)).toBeInViewport()
    await expect(sendButton(page)).toBeInViewport()
  })

  test('the Send button sends too; Shift+Enter starts a new line instead of sending', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const before = (await webConversation()).length
    await composer(page).click()
    await page.keyboard.type('First line')
    await page.keyboard.press('Shift+Enter')
    await page.keyboard.type('Second line')
    await expect(composer(page)).toHaveValue('First line\nSecond line')
    expect((await webConversation()).length, 'Shift+Enter must not send').toBe(before)

    await sendButton(page).click()
    await expect(page.getByText('You said: "Second line"')).toBeVisible()
    expect(await eventsWithText('First line\nSecond line')).toHaveLength(1)
  })

  test('the composer and Send work from the keyboard alone', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await composer(page).focus()
    await page.keyboard.type('Typed without a mouse')
    await page.keyboard.press('Tab')
    await expect(sendButton(page)).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByText('You said: "Typed without a mouse"')).toBeVisible()
  })

  test('empty or whitespace-only messages cannot be sent from the page', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const before = (await webConversation()).length
    await expect(sendButton(page)).toBeDisabled()

    await composer(page).fill('   \n\t  ')
    await expect(sendButton(page)).toBeDisabled()
    await composer(page).press('Enter')
    await composer(page).fill('')
    await composer(page).press('Enter')

    await expect(page.getByLabel('Ava is typing')).toHaveCount(0)
    expect((await webConversation()).length).toBe(before)
  })

  test('the chat API refuses an empty message', async ({ api, owner }) => {
    void owner
    const r = await api.post('/app/chat', { text: '' })
    expect(r.status()).toBe(422)
    expect((await webConversation()).filter((e) => e.content === '')).toHaveLength(0)
  })

  test('the chat API refuses a whitespace-only message and stores nothing', async ({ api, owner }) => {
    // Was a 500 (IndexError in the offline responder) until fixed on 21 October 2026.
    void owner
    const before = (await webConversation()).length
    const r = await api.post('/app/chat', { text: '   ' })
    expect(r.status(), await r.text()).toBe(422)
    expect((await r.json()).detail).toBe('Type a message first.')
    expect((await webConversation()).length).toBe(before)
  })

  test('a message at the 4,000-character limit is sent, stored in full and answered', async ({ page, owner }) => {
    void owner
    const words = 'The quarterly plan needs another pass before Friday. '
    const text = words.repeat(100).slice(0, 3999).trimEnd().padEnd(4000, '.')
    expect(text).toHaveLength(4000)

    await openChat(page)
    await composer(page).fill(text)
    await sendButton(page).click()
    await expect(page.getByText(OFFLINE_REPLY).last()).toBeVisible()
    const stored = await eventsWithText(text)
    expect(stored).toHaveLength(1)
    expect(stored[0].content).toHaveLength(4000)

    // The composer grew but stays a sensible size, and nothing pushes the page sideways.
    await composer(page).fill(text)
    const height = await composer(page).evaluate((el) => el.getBoundingClientRect().height)
    expect(height).toBeLessThanOrEqual(222)
    await expectNoSidewaysScroll(page)
  })

  test('a message over 4,000 characters is refused with a message that says why', async ({ page, owner }) => {
    void owner
    const text = 'x '.repeat(2100).trim() // 4,199 characters
    await openChat(page)
    const before = (await webConversation()).length
    await composer(page).fill(text)
    await sendButton(page).click()
    await expect(page.getByText(/4,?000 characters/)).toBeVisible()
    expect((await webConversation()).length, 'nothing stored').toBe(before)
    // The page still works afterwards.
    await composer(page).fill('A shorter one, then')
    await composer(page).press('Enter')
    await expect(page.getByText('You said: "A shorter one, then"')).toBeVisible()
    await expect(page.getByText(/4,?000 characters/)).toHaveCount(0)
  })

  test('a refused message keeps its draft and is not shown as sent', async ({ page, owner }) => {
    void owner
    const text = 'y '.repeat(2100).trim()
    await openChat(page)
    await composer(page).fill(text)
    await sendButton(page).click()
    await expect(page.getByText(/4,?000 characters/)).toBeVisible()
    await expect(composer(page), 'the person can shorten what they wrote').toHaveValue(text)
    // The words stay in the box to shorten; the conversation shows no "sent" bubble.
    await expect(page.locator('[aria-live="polite"]').getByText(text, { exact: true }), 'not shown as a sent message').toHaveCount(0)
  })

  test('a long unbroken link wraps inside its bubble instead of overflowing the chat', async ({ page, owner }) => {
    void owner
    const link = `https://example.com/${'a'.repeat(400)}`
    await openChat(page)
    await composer(page).fill(link)
    await composer(page).press('Enter')
    const bubble = page.getByText(link, { exact: true })
    await expect(bubble).toBeVisible()
    const fits = await bubble.evaluate((el) => {
      const scroller = el.closest('.overflow-y-auto') as HTMLElement
      return { bubble: el.scrollWidth <= el.clientWidth + 1, chat: scroller.scrollWidth <= scroller.clientWidth + 1 }
    })
    expect(fits).toEqual({ bubble: true, chat: true })
  })

  test('script and markup in a message are shown as text and never run', async ({ page, owner }) => {
    void owner
    const dialogs: string[] = []
    page.on('dialog', (d) => {
      dialogs.push(d.message())
      void d.dismiss()
    })
    const text = '<img src=x onerror="window.__xss=1"><script>window.__xss=2;alert(1)</script>'
    await openChat(page)
    await composer(page).fill(text)
    await composer(page).press('Enter')
    await expect(page.getByText(text, { exact: true })).toBeVisible()
    await expect(page.getByText(`You said: "${text}"`)).toBeVisible()
    await page.reload()
    await expect(page.getByText(text, { exact: true })).toBeVisible()
    expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined()
    expect(dialogs).toEqual([])
  })

  test('double-clicking Send sends the message once', async ({ page, owner }) => {
    void owner
    const text = 'Only once, please'
    await openChat(page)
    await composer(page).fill(text)
    await sendButton(page).dblclick()
    await expect(page.getByText(`You said: "${text}"`)).toBeVisible()
    await expect(page.getByText(text, { exact: true })).toHaveCount(1)
    expect(await eventsWithText(text)).toHaveLength(1)
  })

  test('if the API cannot be reached, the person is told and can send again', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await page.route('**/api/app/chat', (route) => route.abort('connectionrefused'))
    await composer(page).fill('Lost in the network')
    await composer(page).press('Enter')
    await expect(page.getByText(/Can't reach Superaide/)).toBeVisible()
    // Not sent: the words come back to the box and no bubble pretends otherwise.
    await expect(composer(page)).toHaveValue('Lost in the network')
    await expect(page.locator('[aria-live="polite"]').getByText('Lost in the network', { exact: true })).toHaveCount(0)
    await expect(page.getByLabel('Ava is typing')).toHaveCount(0)

    await page.unroute('**/api/app/chat')
    await composer(page).press('Enter') // send the same words again
    await expect(page.getByText('You said: "Lost in the network"')).toBeVisible()
    await expect(page.getByText(/Can't reach Superaide/)).toHaveCount(0)
  })

  test('a message sent before the history has loaded stays on screen with its reply', async ({ page, owner }) => {
    // W6, fixed by a363326: ChatPage keeps "local-" messages when the late history lands, and
    // ignores that history once a fresher /messages has been read.
    void owner
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => (release = resolve))
    let calls = 0
    await page.route('**/api/app/messages', async (route) => {
      calls += 1
      if (calls > 1) return route.continue()
      const response = await route.fetch() // the first load reads the history now…
      await held // …but arrives late
      await route.fulfill({ response })
    })
    await page.goto('/dashboard/chat')
    await expect(composer(page)).toBeVisible()
    const reloaded = page.waitForResponse((r) => r.url().includes('/api/app/messages'))
    await composer(page).fill('Sent before the history arrived')
    await composer(page).press('Enter')
    await reloaded
    release()
    await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
    await expect(page.getByText('Sent before the history arrived', { exact: true })).toBeVisible()
    await expect(page.getByText('You said: "Sent before the history arrived"')).toBeVisible()
  })

  test('quick actions fill the composer and focus it, without sending', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const before = (await webConversation()).length
    const cases: [string, string][] = [
      ['How would you handle…', 'If someone messages you saying '],
      ['Draft a reply', 'Help me reply to this message:\n\n'],
      ['Remember something', 'Remember that '],
    ]
    for (const [label, prefill] of cases) {
      await page.getByRole('button', { name: label }).click()
      await expect(composer(page)).toHaveValue(prefill)
      await expect(composer(page)).toBeFocused()
    }
    // A quick action replaces the draft rather than appending to it.
    await composer(page).fill('half-written thought')
    await page.getByRole('button', { name: 'Draft a reply' }).click()
    await expect(composer(page)).toHaveValue('Help me reply to this message:\n\n')
    expect((await webConversation()).length, 'filling is not sending').toBe(before)
  })

  test('"What do you know about me?" asks straight away', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await page.getByRole('button', { name: 'What do you know about me?' }).click()
    await expect(page.getByText('What do you know about me so far?', { exact: true })).toBeVisible()
    await expect(page.getByText('You said: "What do you know about me so far?"')).toBeVisible()
    expect(await eventsWithText('What do you know about me so far?')).toHaveLength(1)
  })

  test('logging out and back in brings the conversation back', async ({ page, owner }) => {
    await openChat(page)
    await composer(page).fill('Note for later: call the bank')
    await composer(page).press('Enter')
    await expect(page.getByText('You said: "Note for later: call the bank"')).toBeVisible()

    await logOutFromSidebar(page, owner)
    // The session is over on the server too, not just in the page.
    expect((await page.request.get('/api/app/messages')).status()).toBe(401)
    await page.goto('/dashboard/chat')
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard%2Fchat$/)
    await page.getByLabel('Email').fill(owner.email)
    await page.getByLabel('Password', { exact: true }).fill(owner.password)
    const loggedIn = page.waitForResponse((r) => r.url().endsWith('/api/auth/login'))
    await page.getByRole('button', { name: 'Log in' }).click()
    expect((await loggedIn).ok()).toBe(true)
    // Where the login page lands is racy (see the next test), so open the chat directly.
    await openChat(page)
    await expect(page.getByText('Note for later: call the bank', { exact: true })).toBeVisible()
  })

  test('logging in from /login?next=/dashboard/chat lands in the chat even when the agent loads slowly', async ({ page, owner }) => {
    await page.context().clearCookies()
    await page.goto('/login?next=%2Fdashboard%2Fchat')
    // A slow /app/me (as on a busy laptop) makes the race visible every time.
    await page.route('**/api/app/me', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      await route.continue()
    })
    const visited: string[] = []
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname)
    })
    await page.getByLabel('Email').fill(owner.email)
    await page.getByLabel('Password', { exact: true }).fill(owner.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page.getByRole('heading', { level: 1, name: greeting(owner.name) })).toBeVisible()
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
    expect(visited, 'straight to the chat, never via the home page').not.toContain('/')
  })
})

test.describe('workspace: notebook', () => {
  test('lists what Ava learned in the introduction, with where each fact came from', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeVisible()
    await expect(page.getByText('Everything Ava remembers about you, and where she learned it. Only you can see it.')).toBeVisible()

    const about = notebookFact(page, 'says: I lead applied AI at PebbleRoad in Singapore.')
    await expect(about).toContainText('About you')
    await expect(about).toContainText('from your introduction')
    const priya = notebookFact(page, "Priya is Shashank Rao's partner")
    await expect(priya).toContainText('People')
    await expect(priya).toContainText('from your introduction')
    await expect(notebookFact(page, 'quiet hours are 22:00–07:00')).toContainText('Availability')
    await expect(notebookFact(page, 'never shared with anyone: My home address')).toContainText('Sharing')

    // Every live fact about the owner is listed, and only those.
    const live = (await memories()).filter((m) => m.valid_to === null)
    await expect(page.getByRole('button', { name: 'Forget this' })).toHaveCount(live.length)
  })

  test('"Remember that…" in chat adds a note from chat', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await page.getByRole('button', { name: 'Remember something' }).click()
    // The quick action focuses the composer on the next tick; type only once it has.
    await expect(composer(page)).toHaveValue('Remember that ')
    await expect(composer(page)).toBeFocused()
    await page.keyboard.type("my sister's birthday is 3 March")
    await page.keyboard.press('Enter')
    await expect(page.getByText("Saved to your notebook. Only you can see it.")).toBeVisible()
    await goToDashboardPage(page, 'Notebook')
    await expect(page).toHaveURL(/\/dashboard\/notebook$/)

    const fact = notebookFact(page, "my sister's birthday is 3 March")
    await expect(fact).toBeVisible()
    await expect(fact).toContainText('Note')
    await expect(fact).toContainText('from chat')
    const stored = (await memories()).find((m) => m.content === "my sister's birthday is 3 March")
    expect(stored?.source).toBe('chat')

    await page.reload()
    await expect(notebookFact(page, "my sister's birthday is 3 March")).toBeVisible()
  })

  test('"Forget this" asks first, and Keep leaves the fact alone', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    const fact = notebookFact(page, "Arjun is Shashank Rao's team")
    await fact.getByRole('button', { name: 'Forget this' }).click()
    await expect(fact.getByText("Ava won't use this again.")).toBeVisible()
    await fact.getByRole('button', { name: 'Keep' }).click()
    await expect(fact.getByRole('button', { name: 'Forget this' })).toBeVisible()
    const arjun = (await memories()).find((m) => m.content.startsWith('Arjun is'))
    expect(arjun?.valid_to).toBeNull()
  })

  test('"Forget this" removes the fact from the notebook and from what Ava uses', async ({ page, api, owner }) => {
    void owner
    await openNotebookPage(page)
    const fact = notebookFact(page, "Arjun is Shashank Rao's team")
    await expect(fact).toBeVisible()
    const forgetButtons = page.getByRole('button', { name: 'Forget this' })
    const before = (await memories()).length
    await expect(forgetButtons).toHaveCount(before)
    await fact.getByRole('button', { name: 'Forget this' }).click()
    await fact.getByRole('button', { name: 'Forget', exact: true }).click()

    await expect(notebookFact(page, "Arjun is Shashank Rao's team")).toHaveCount(0)
    await expect(forgetButtons).toHaveCount(before - 1)
    const arjun = (await memories()).find((m) => m.content.startsWith('Arjun is'))
    expect(arjun?.valid_to, 'retired in the database').not.toBeNull()
    expect(await auditActions()).toContain('memory_forgotten')

    await page.reload()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeVisible()
    await expect(notebookFact(page, "Arjun is Shashank Rao's team")).toHaveCount(0)

    // Forgetting it twice is refused plainly.
    const again = await api.post(`/app/notebook/${arjun!.id}/forget`)
    expect(again.status()).toBe(404)
    expect((await again.json()).detail).toBe('Not found in your notebook')
  })

  test('forgetting something that is not in the notebook is refused', async ({ api, owner }) => {
    void owner
    const unknown = await api.post('/app/notebook/00000000-0000-4000-8000-000000000000/forget')
    expect(unknown.status()).toBe(404)
    const malformed = await api.post('/app/notebook/not-a-real-id/forget')
    expect(malformed.status()).toBe(422)
    const traversal = await api.post('/app/notebook/..%2F..%2Fauth%2Flogout/forget')
    expect(traversal.status()).toBeGreaterThanOrEqual(400)
    expect(traversal.status()).toBeLessThan(500)
    expect((await memories()).every((m) => m.valid_to === null)).toBe(true)
  })

  test('telling Ava the same thing twice keeps one note', async ({ page, owner }) => {
    void owner
    await openChat(page)
    for (let n = 0; n < 2; n += 1) {
      await composer(page).fill('Remember that I never take calls before 9am')
      await composer(page).press('Enter')
      await expect(page.getByText("Saved to your notebook. Only you can see it.").nth(n)).toBeVisible()
    }
    await openNotebookPage(page)
    await expect(notebookFact(page, 'I never take calls before 9am')).toHaveCount(1)
  })

  test('"Remember that" with nothing after it does not save a note', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await page.getByRole('button', { name: 'Remember something' }).click()
    await sendButton(page).click()
    await expect(page.getByText('Remember that', { exact: true })).toBeVisible()
    await expect(page.getByLabel('Ava is typing')).toHaveCount(0)
    expect((await memories()).map((m) => m.content)).not.toContain('that')
  })
})

test.describe('workspace: offline mode', () => {
  test('the offline notice links to Connections, where Claude can be connected', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const notice = page.getByText("Ava's Claude brain isn't connected yet")
    await expect(notice).toBeVisible()
    await expect(notice).toContainText('replies are placeholders')
    await expect(page.getByRole('main').getByText('Ava: offline mode')).toBeVisible()

    // The notice's link opens Connections, and Claude is the first thing in it.
    await notice.getByRole('link', { name: 'Connect Claude' }).click()
    await expect(page).toHaveURL(/\/dashboard\/connections$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Connections' })).toBeVisible()
    const claude = page.getByRole('button', { name: /Claude, Ava's brain/ })
    await expect(claude).toBeVisible()
    await claude.click()
    await expect(page.getByLabel('Claude API key')).toBeVisible()
  })

  test('the dashboard home says Ava is in offline mode and links to Connections', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await expect(page.getByText('Ava is in offline mode, so her replies are placeholders for now.')).toBeVisible()
    await page.getByRole('main').getByRole('link', { name: 'Connect Claude' }).click()
    await expect(page).toHaveURL(/\/dashboard\/connections$/)
  })
})

test.describe('workspace: privacy between accounts', () => {
  test("another account can't read or change this owner's chat, notebook or connections", async ({ page, browser, owner }) => {
    await openChat(page)
    await composer(page).fill('Remember that the acquisition closes in May')
    await composer(page).press('Enter')
    await expect(page.getByText("Saved to your notebook. Only you can see it.")).toBeVisible()
    const secret = (await memories()).find((m) => m.content === 'the acquisition closes in May')!

    const other = await newVisitor(browser)
    try {
      await other.api.signup({ name: 'Mallory Ng', email: `e2e-mallory-${Date.now()}@example.com`, password: owner.password })

      const hire = await other.api.post('/app/hire', { agent: 'ava', accepted_notice: true })
      expect(hire.status()).toBe(409)
      // dc2e12a: a second account is no longer told the owner's name.
      const refusal = (await hire.json()).detail as string
      expect(refusal).toBe('Ava already works for someone else.')
      expect(refusal).not.toContain(owner.name.split(' ')[0])

      for (const path of ['/app/messages', '/app/notebook', '/app/me', '/app/connections']) {
        const r = await other.api.get(path)
        expect(r.status(), path).toBe(403)
        const body = await r.text()
        expect(body).not.toContain('acquisition')
        expect(body).not.toContain('PebbleRoad')
      }
      const writes: [string, unknown][] = [
        ['/app/chat', { text: 'What did my boss tell you?' }],
        [`/app/notebook/${secret.id}/forget`, {}],
        ['/app/connections/anthropic', { api_key: 'not-a-claude-key-at-all' }],
        ['/app/connections/telegram/disconnect', {}],
      ]
      for (const [path, data] of writes) {
        const r = await other.api.post(path, data)
        expect(r.status(), path).toBe(403)
      }
      expect((await memories()).find((m) => m.id === secret.id)?.valid_to).toBeNull()
      expect(await eventsWithText('What did my boss tell you?')).toHaveLength(0)

      // In the browser they are sent away from the dashboard without seeing anything.
      for (const path of ['/dashboard/chat', '/dashboard/notebook', '/workspace']) {
        await other.page.goto(path)
        await expect(other.page).toHaveURL(/\/$/)
        await expect(other.page.getByText('the acquisition closes in May')).toHaveCount(0)
        await expect(other.page.getByText('PebbleRoad')).toHaveCount(0)
      }
    } finally {
      await other.context.close()
    }
  })

  test('a signed-out visitor or a forged session gets nothing', async ({ browser, owner }) => {
    void owner
    const stranger = await newVisitor(browser)
    try {
      for (const path of ['/app/messages', '/app/notebook', '/app/connections']) {
        const r = await stranger.api.get(path)
        expect(r.status(), path).toBe(401)
      }
      await stranger.context.addCookies([
        { name: 'superaide_session', value: 'forged-session-token-1234567890', url: test.info().project.use.baseURL! },
      ])
      const forged = await stranger.api.get('/app/messages')
      expect(forged.status()).toBe(401)
      const chat = await stranger.api.post('/app/chat', { text: 'hello?' })
      expect(chat.status()).toBe(401)
      expect(await eventsWithText('hello?')).toHaveLength(0)
    } finally {
      await stranger.context.close()
    }
  })

  test('changes without the anti-forgery header are refused', async ({ api, owner }) => {
    void owner
    const r = await api.postWithoutCsrfHeader('/app/chat', { text: 'cross-site' })
    expect(r.status()).toBe(403)
    expect(await eventsWithText('cross-site')).toHaveLength(0)
  })
})

// --- Connections ------------------------------------------------------------------------

function connectionRow(page: import('@playwright/test').Page, name: RegExp) {
  return page.getByRole('button', { name })
}

const SERVICES = [
  {
    row: /Claude, Ava's brain/,
    step: 'Open the Claude Console and sign in.',
    link: 'Open the Claude Console',
    href: /^https:\/\/platform\.claude\.com\/dashboard$/,
    fields: ['Claude API key'],
  },
  {
    row: /^Telegram/,
    step: 'Open @BotFather in Telegram and send /newbot.',
    link: 'Open BotFather',
    href: /^https:\/\/t\.me\/BotFather$/,
    fields: ['Bot token from BotFather'],
  },
  {
    row: /^Slack/,
    step: 'Open the link: Slack creates your own Ava app with everything set up.',
    link: 'Create my Slack app',
    href: /^https:\/\/api\.slack\.com\/apps\?new_app=1/,
    fields: ['Bot User OAuth Token', 'App-level token'],
  },
]

test.describe('connections', () => {
  test('each service opens its own setup guide, one at a time', async ({ page, owner }) => {
    void owner
    const outside = await recordOutsideCalls(page)
    await openConnections(page)
    for (const s of SERVICES) {
      const row = connectionRow(page, s.row)
      await expect(row).toHaveAttribute('aria-expanded', 'false')
      await row.click()
      await expect(row).toHaveAttribute('aria-expanded', 'true')
      await expect(page.getByText(s.step)).toBeVisible()
      const link = page.getByRole('link', { name: s.link })
      await expect(link).toHaveAttribute('href', s.href)
      await expect(link).toHaveAttribute('target', '_blank')
      await expect(link).toHaveAttribute('rel', /noopener/)
      for (const f of s.fields) await expect(page.getByLabel(f)).toBeVisible()
      // Only the open service's guide is showing.
      for (const other of SERVICES.filter((o) => o !== s)) {
        await expect(page.getByText(other.step)).toHaveCount(0)
      }
    }
    // Pressing the open row again closes it.
    const slack = connectionRow(page, /^Slack/)
    await slack.click()
    await expect(slack).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByText(SERVICES[2].step)).toHaveCount(0)
    expect(outside).toEqual([])
  })

  test('keys go into password fields, and "Check and save" waits for every field', async ({ page, owner }) => {
    void owner
    await openConnections(page)
    for (const s of SERVICES) {
      await connectionRow(page, s.row).click()
      const save = page.getByRole('button', { name: 'Check and save' })
      await expect(save).toHaveCount(1) // the previous form has finished closing
      await expect(save).toBeDisabled()
      for (const [n, f] of s.fields.entries()) {
        const input = page.getByLabel(f)
        await expect(input).toHaveAttribute('type', 'password')
        await expect(input).toHaveAttribute('autocomplete', 'off')
        await input.fill('   ')
        await expect(save, 'spaces are not a key').toBeDisabled()
        await input.fill('something')
        if (n < s.fields.length - 1) await expect(save, 'every field is needed').toBeDisabled()
      }
      await expect(save).toBeEnabled()
      await expect(page.getByText('Kept encrypted in environment. Never shown again.')).toBeVisible()
    }
  })

  const WRONG = [
    {
      service: 'Claude',
      row: /Claude, Ava's brain/,
      fill: { 'Claude API key': 'this-is-not-a-claude-key' },
      message: "That doesn't look like a Claude API key. Keys start with sk-ant-.",
    },
    {
      service: 'Telegram',
      row: /^Telegram/,
      fill: { 'Bot token from BotFather': 'definitely not a bot token' },
      message: "That doesn't look like a bot token. BotFather sends one like 123456789:AAE…",
    },
    {
      service: 'Slack',
      row: /^Slack/,
      fill: { 'Bot User OAuth Token': 'not-a-slack-bot-token', 'App-level token': 'not-a-slack-app-token' },
      message: 'The bot token should start with xoxb- (OAuth & Permissions page).',
    },
  ]

  for (const w of WRONG) {
    test(`a ${w.service} key in the wrong format is refused with a clear message, and nothing is stored or sent`, async ({ page, owner }) => {
      void owner
      const outside = await recordOutsideCalls(page)
      await openConnections(page)
      await connectionRow(page, w.row).click()
      for (const [label, value] of Object.entries(w.fill)) await page.getByLabel(label).fill(value)

      const response = page.waitForResponse((r) => r.url().includes('/api/app/connections/') && r.request().method() === 'POST')
      await page.getByRole('button', { name: 'Check and save' }).click()
      const r = await response
      expect(r.status()).toBe(400)
      const body = await r.text()
      for (const value of Object.values(w.fill)) expect(body, 'the key is not echoed back').not.toContain(value)

      await expect(page.getByText(w.message)).toBeVisible()
      const shown = await page.locator('body').innerText()
      for (const value of Object.values(w.fill)) expect(shown, 'the key is not shown on the page').not.toContain(value)
      // Still not connected, and the fields are still hidden.
      await expect(connectionRow(page, w.row)).toContainText('Connect')
      await expect(connectionRow(page, w.row)).not.toContainText('Connected')
      for (const label of Object.keys(w.fill)) await expect(page.getByLabel(label)).toHaveAttribute('type', 'password')

      expect(await storedSecrets()).toEqual([])
      expect(await auditActions()).not.toContain('connected')
      expect(outside).toEqual([])
    })
  }

  test('the connections API refuses wrong-format keys without storing them', async ({ api, owner }) => {
    void owner
    const cases: [string, Record<string, string>, string][] = [
      ['anthropic', { api_key: 'sk_ant_with_underscores' }, 'Keys start with sk-ant-.'],
      ['telegram', { bot_token: '123:shorttoken' }, "doesn't look like a bot token"],
      ['telegram', { bot_token: '<script>alert(1)</script>' }, "doesn't look like a bot token"],
      ['slack', { bot_token: 'xoxp-user-token-wrong-kind', app_token: 'not-reached-either' }, 'should start with xoxb-'],
    ]
    for (const [provider, body, message] of cases) {
      const r = await api.post(`/app/connections/${provider}`, body)
      expect(r.status(), `${provider} ${JSON.stringify(body)}`).toBe(400)
      const detail = (await r.json()).detail as string
      expect(detail).toContain(message)
      for (const v of Object.values(body)) expect(detail).not.toContain(v)
    }
    // Too short, too long, or missing fields: refused before any check.
    expect((await api.post('/app/connections/anthropic', { api_key: 'short' })).status()).toBe(422)
    expect((await api.post('/app/connections/anthropic', { api_key: 'k'.repeat(301) })).status()).toBe(422)
    expect((await api.post('/app/connections/slack', { bot_token: 'only-the-bot-token' })).status()).toBe(422)
    expect((await api.post('/app/connections/whatsapp', { token: 'anything-at-all' })).status()).toBe(404)
    expect(await storedSecrets()).toEqual([])
  })

  test('a key that is too short is refused with a message a person can act on', async ({ page, owner }) => {
    void owner
    await openConnections(page)
    await connectionRow(page, /Claude, Ava's brain/).click()
    await page.getByLabel('Claude API key').fill('sk-ant')
    await page.getByRole('button', { name: 'Check and save' }).click()
    await expect(page.getByText(/at least 10 characters/)).toBeVisible()
    expect(await storedSecrets()).toEqual([])
  })

  test('while a key is being checked the button says so and cannot be pressed twice', async ({ page, owner }) => {
    void owner
    await openConnections(page)
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => (release = resolve))
    let calls = 0
    await page.route('**/api/app/connections/anthropic', async (route) => {
      calls += 1
      await held
      await route.continue()
    })
    await connectionRow(page, /Claude, Ava's brain/).click()
    await page.getByLabel('Claude API key').fill('still-not-a-claude-key')
    await page.getByRole('button', { name: 'Check and save' }).click()
    const checking = page.getByRole('button', { name: 'Checking…' })
    await expect(checking).toBeDisabled()
    await checking.click({ force: true })
    release()
    await expect(page.getByText("That doesn't look like a Claude API key.")).toBeVisible()
    await expect(page.getByRole('button', { name: 'Check and save' })).toBeEnabled()
    expect(calls).toBe(1)
  })

  test('if connections cannot be loaded, the panel says so instead of spinning', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/connections', (route) => route.abort('connectionrefused'))
    await openConnections(page)
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
    await expect(page.getByText(/Can't reach Superaide/)).toBeVisible()
    await expect(page.getByText('Checking connections…')).toHaveCount(0)
    // The rest of the dashboard still works.
    await goToDashboardPage(page, 'Chat')
    await expect(page.getByText(INTRO_LINE, { exact: true })).toBeVisible()
    await composer(page).fill('Still here?')
    await composer(page).press('Enter')
    await expect(page.getByText('You said: "Still here?"')).toBeVisible()
  })

  test('"Disconnect" on something that is not connected is harmless', async ({ page, api, owner }) => {
    void owner
    await openConnections(page)
    // Nothing is connected, so the page offers no Disconnect at all.
    for (const s of SERVICES) {
      await connectionRow(page, s.row).click()
      await expect(page.getByRole('button', { name: /^Disconnect/ })).toHaveCount(0)
    }
    const before = await (await api.get('/app/connections')).json()
    for (const provider of ['anthropic', 'telegram', 'slack']) {
      const r = await api.post(`/app/connections/${provider}/disconnect`)
      expect(r.status(), provider).toBe(200)
      expect((await r.json()).services).toEqual(before.services)
    }
    const unknown = await api.post('/app/connections/whatsapp/disconnect')
    expect(unknown.status()).toBe(404)
    expect((await unknown.json()).detail).toBe('Unknown connection')
    const traversal = await api.post('/app/connections/..%2Fnotebook/disconnect')
    expect(traversal.status()).toBeGreaterThanOrEqual(400)
    expect(traversal.status()).toBeLessThan(500)

    expect(await storedSecrets()).toEqual([])
    // The dashboard still works and still shows nothing connected.
    await page.reload()
    await expect(connectionRow(page, /Claude, Ava's brain/)).toContainText('Real replies and reading your files')
    await openChat(page)
    await composer(page).fill('Still answering?')
    await composer(page).press('Enter')
    await expect(page.getByText('You said: "Still answering?"')).toBeVisible()
  })

  test('a saved key is only ever shown as a hint, and Disconnect removes it', async ({ page, api, owner }) => {
    void owner
    const key = 'sk-ant-e2e-planted-placeholder-not-real-7f3a'
    await plantSecret('anthropic', 'api_key', key)
    await openConnections(page)
    const claude = connectionRow(page, /Claude, Ava's brain/)
    await expect(claude).toContainText('Connected (sk-…7f3a)')

    const status = await api.get('/app/connections')
    const text = await status.text()
    expect(text).not.toContain(key)
    expect(text).not.toContain('placeholder-not-real')
    expect((await status.json()).services.anthropic).toMatchObject({ connected: true, via: 'app', hints: { api_key: 'sk-…7f3a' } })

    await claude.click()
    expect(await page.locator('body').innerText()).not.toContain(key)
    await expect(page.getByLabel('Claude API key'), 'no form to re-enter while connected').toHaveCount(0)
    await page.getByRole('button', { name: 'Disconnect Claude' }).click()
    await expect(claude).toContainText('Real replies and reading your files')
    expect(await storedSecrets()).toEqual([])
    expect(await auditActions()).toContain('disconnected')
  })

  test('WhatsApp is shown as unavailable, with the reason', async ({ page, owner }) => {
    void owner
    await openConnections(page)
    await expect(page.getByText('WhatsApp', { exact: true })).toBeVisible()
    await expect(page.getByText("Meta's WhatsApp Business terms don't allow general-purpose AI assistants")).toBeVisible()
    await expect(page.getByText('Not available')).toBeVisible()
    await expect(page.getByRole('button', { name: /WhatsApp/ })).toHaveCount(0)
  })
})

// Store a credential the way the vault does (AES-256-GCM, slot as associated data),
// so the connected state can be tested without calling the real service.
async function plantSecret(provider: string, name: string, value: string): Promise<void> {
  const key = Buffer.from(apiEnv().SECRETS_MASTER_KEY, 'base64')
  const nonce = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', key, nonce)
  cipher.setAAD(Buffer.from(`${provider}/${name}`))
  const ct = Buffer.concat([cipher.update(value, 'utf8'), cipher.final(), cipher.getAuthTag()])
  const token = Buffer.concat([Buffer.from([1]), nonce, ct]).toString('base64')
  const prefix = value.slice(0, 8).includes('-') ? `${value.split('-')[0]}-` : value.slice(0, 3)
  const hint = `${prefix}…${value.slice(-4)}`
  await query(
    `INSERT INTO integration_secret (id, tenant_id, provider, name, ciphertext, hint, meta, updated_at, created_at)
     SELECT gen_random_uuid(), tenant_id, $1, $2, $3, $4, '{}'::jsonb, now(), now() FROM user_account LIMIT 1`,
    [provider, name, token, hint],
  )
}
