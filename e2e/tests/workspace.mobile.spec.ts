// The workspace on a phone (Pixel 7): one view at a time, picked from the switcher
// in the top bar (Chat, Notebook, Ava).
import type { Page } from '@playwright/test'
import { expect, test } from '../src/fixtures'
import { composer, expectNoSidewaysScroll, memories, notebookFact, openWorkspace, sendButton } from '../src/helpers/workspace'

function view(page: Page, name: 'Chat' | 'Notebook' | 'Ava') {
  return page.getByRole('banner').getByRole('button', { name, exact: true })
}

test.describe('workspace on a phone', () => {
  test('chat fits the screen, and a message gets a reply', async ({ page, owner }) => {
    await openWorkspace(page)
    await expect(page.getByRole('heading', { level: 1, name: `Hello, ${owner.name.split(' ')[0]}. What shall we work on?` })).toBeVisible()
    for (const name of ['Chat', 'Notebook', 'Ava'] as const) await expect(view(page, name)).toBeVisible()
    // Only the chat is showing.
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()
    await expect(composer(page)).toBeInViewport()
    await expect(sendButton(page)).toBeInViewport()

    await composer(page).tap()
    await composer(page).fill('Remind me about the dentist on Friday')
    await sendButton(page).tap()
    await expect(page.getByText('You said: "Remind me about the dentist on Friday"')).toBeVisible()
    await expect(composer(page)).toHaveValue('')
    await expect(composer(page)).toBeInViewport()
    await expectNoSidewaysScroll(page)
  })

  test('quick actions scroll sideways inside their row, not the page', async ({ page, owner }) => {
    void owner
    await openWorkspace(page)
    const last = page.getByRole('button', { name: 'What do you know about me?' })
    await last.scrollIntoViewIfNeeded()
    await expect(last).toBeInViewport()
    await page.getByRole('button', { name: 'Remember something' }).tap()
    await expect(composer(page)).toHaveValue('Remember that ')
    await expectNoSidewaysScroll(page)
  })

  test('the notebook can be read and edited from its own view', async ({ page, owner }) => {
    void owner
    await openWorkspace(page)
    await view(page, 'Notebook').tap()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeVisible()
    await expect(composer(page)).toBeHidden()
    // The notebook view is the notebook, not the Connections panel.
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeHidden()

    const priya = notebookFact(page, "Priya is Shashank Rao's partner")
    await expect(priya).toBeVisible()
    await expect(priya).toContainText('from your introduction')

    const arjun = notebookFact(page, "Arjun is Shashank Rao's team")
    await arjun.getByRole('button', { name: 'Forget this' }).tap()
    await arjun.getByRole('button', { name: 'Forget', exact: true }).tap()
    await expect(notebookFact(page, "Arjun is Shashank Rao's team")).toHaveCount(0)
    expect((await memories()).find((m) => m.content.startsWith('Arjun is'))?.valid_to).not.toBeNull()
    await expectNoSidewaysScroll(page)

    await view(page, 'Chat').tap()
    await expect(composer(page)).toBeVisible()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()
  })

  test('a note told in chat appears in the notebook view', async ({ page, owner }) => {
    void owner
    await openWorkspace(page)
    await composer(page).fill('Remember that I park on level B2')
    await sendButton(page).tap()
    await expect(page.getByText("Noted. I'll only bring that up with you, in private.")).toBeVisible()
    await view(page, 'Notebook').tap()
    await expect(notebookFact(page, 'I park on level B2')).toContainText('from chat')
  })

  test('a draft survives switching views', async ({ page, owner }) => {
    void owner
    await openWorkspace(page)
    await composer(page).fill('Drafted, then wandered off')
    await view(page, 'Notebook').tap()
    await view(page, 'Chat').tap()
    await expect(composer(page), 'the draft survives switching views').toHaveValue('Drafted, then wandered off')
  })

  test('Connections are under the Ava view, with every service', async ({ page, owner }) => {
    void owner
    await openWorkspace(page)
    await view(page, 'Ava').tap()
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()
    await page.getByRole('button', { name: /Claude, Ava's brain/ }).tap()
    await expect(page.getByLabel('Claude API key')).toBeVisible()
    await expect(page.getByLabel('Claude API key')).toHaveAttribute('type', 'password')
    await expect(page.getByText('WhatsApp', { exact: true })).toBeVisible()
    await expectNoSidewaysScroll(page)
  })

  test('the offline notice tells a phone user where Connections is', async ({ page, owner }) => {
    test.fail(true, 'BUG: on a phone the offline notice says Connections is "on the right", but it is under the Ava view')
    void owner
    await openWorkspace(page)
    const notice = page.getByText("Ava's Claude brain isn't connected yet")
    await expect(notice).toBeVisible()
    await expect(notice).not.toContainText('on the right')
  })

  test('the view switcher tells assistive technology which view is showing', async ({ page, owner }) => {
    test.fail(true, 'BUG: the Chat/Notebook/Ava switcher marks the current view only with styling (no aria-pressed/aria-selected/aria-current)')
    void owner
    await openWorkspace(page)
    const current = async (name: 'Chat' | 'Notebook' | 'Ava') => {
      const b = view(page, name)
      return (
        (await b.getAttribute('aria-pressed')) === 'true' ||
        (await b.getAttribute('aria-selected')) === 'true' ||
        ['true', 'page'].includes((await b.getAttribute('aria-current')) ?? '')
      )
    }
    expect(await current('Chat')).toBe(true)
    await view(page, 'Notebook').tap()
    expect(await current('Notebook')).toBe(true)
    expect(await current('Chat')).toBe(false)
  })
})
