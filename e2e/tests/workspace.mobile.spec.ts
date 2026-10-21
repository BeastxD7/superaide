// The dashboard on a phone (Pixel 7): one page at a time, picked from the sidebar,
// which opens as a sheet from the "Toggle Sidebar" button in the top bar.
// (This was the /workspace screen with a Chat / Notebook / Ava switcher.)
import { expect, test } from '../src/fixtures'
import {
  composer,
  expectNoSidewaysScroll,
  goToDashboardPage,
  memories,
  notebookFact,
  openChat,
  sendButton,
  sidebarTrigger,
} from '../src/helpers/workspace'

test.describe('dashboard on a phone', () => {
  test('chat fits the screen, and a message gets a reply', async ({ page, owner }) => {
    await openChat(page)
    await expect(page.getByRole('heading', { level: 1, name: `Hello, ${owner.name.split(' ')[0]}. What shall we work on?` })).toBeVisible()
    // The sidebar is tucked away behind its button; only the chat is showing.
    await expect(sidebarTrigger(page)).toBeVisible()
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeHidden()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()

    await composer(page).tap()
    await composer(page).fill('Remind me about the dentist on Friday')
    await sendButton(page).tap()
    // This now really sets a reminder (it used to be echoed back offline).
    await expect(page.getByText(/Got it\. I'll remind you .*: The dentist/)).toBeVisible()
    await expect(composer(page)).toHaveValue('')
    await expectNoSidewaysScroll(page)
  })

  test('with a long conversation, the top bar and the composer stay on screen', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(sidebarTrigger(page)).toBeInViewport()
    await expect(composer(page)).toBeInViewport()
    await expect(sendButton(page)).toBeInViewport()
  })

  test('quick actions scroll sideways inside their row, not the page', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const last = page.getByRole('button', { name: 'What do you know about me?' })
    await last.scrollIntoViewIfNeeded()
    await expect(last).toBeInViewport()
    await page.getByRole('button', { name: 'Remember something' }).tap()
    await expect(composer(page)).toHaveValue('Remember that ')
    await expectNoSidewaysScroll(page)
  })

  test('the notebook can be read and edited from its own page', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await goToDashboardPage(page, 'Notebook')
    await expect(page).toHaveURL(/\/dashboard\/notebook$/)
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeVisible()
    // Picking a page closes the sidebar sheet again.
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeHidden()
    await expect(composer(page)).toBeHidden()
    // The notebook page is the notebook, not Connections.
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

    await goToDashboardPage(page, 'Chat')
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
    await expect(composer(page)).toBeVisible()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()
  })

  test('a note told in chat appears in the notebook', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await composer(page).fill('Remember that I park on level B2')
    await sendButton(page).tap()
    await expect(page.getByText("Saved to your notebook. Only you can see it.")).toBeVisible()
    await goToDashboardPage(page, 'Notebook')
    await expect(notebookFact(page, 'I park on level B2')).toContainText('from chat')
  })

  test('a draft survives opening and closing the sidebar', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await composer(page).fill('Drafted, then wandered off')
    await sidebarTrigger(page).tap()
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeHidden()
    await expect(composer(page), 'the draft survives opening the sidebar').toHaveValue('Drafted, then wandered off')
  })

  test('Connections has its own page, with every service', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await goToDashboardPage(page, 'Connections')
    await expect(page).toHaveURL(/\/dashboard\/connections$/)
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
    await expect(page.getByRole('heading', { name: "Ava's notebook" })).toBeHidden()
    await page.getByRole('button', { name: /Claude, Ava's brain/ }).tap()
    await expect(page.getByLabel('Claude API key')).toBeVisible()
    await expect(page.getByLabel('Claude API key')).toHaveAttribute('type', 'password')
    await expect(page.getByText('WhatsApp', { exact: true })).toBeVisible()
    await expectNoSidewaysScroll(page)
  })

  test('the offline notice tells a phone user where Connections is', async ({ page, owner }) => {
    // W11, fixed by the dashboard: the notice no longer says "on the right"; it links to
    // /dashboard/connections (features/dashboard/ChatPage.tsx).
    void owner
    await openChat(page)
    const notice = page.getByText("Ava's Claude brain isn't connected yet")
    await expect(notice).toBeVisible()
    await expect(notice).not.toContainText('on the right')
    await notice.getByRole('link', { name: 'Connect Claude' }).tap()
    await expect(page).toHaveURL(/\/dashboard\/connections$/)
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
  })

  test('the sidebar tells assistive technology which page is showing', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await sidebarTrigger(page).tap()
    const link = (name: string) => page.getByRole('link', { name, exact: true })
    await expect(link('Chat')).toBeVisible()
    await expect(link('Chat')).toHaveAttribute('aria-current', 'page')
    await expect(link('Notebook')).not.toHaveAttribute('aria-current', 'page')
    await link('Notebook').tap()
    await expect(page).toHaveURL(/\/dashboard\/notebook$/)
    await sidebarTrigger(page).tap()
    await expect(link('Notebook')).toHaveAttribute('aria-current', 'page')
    await expect(link('Chat')).not.toHaveAttribute('aria-current', 'page')
  })
})
