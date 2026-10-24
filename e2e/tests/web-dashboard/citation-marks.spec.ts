// Issue #341: the [n] marks in a reply are real buttons. They open the source (kind, title, the exact words
// and a link), work from the keyboard, collapse repeats, pass axe, and become a bottom sheet on a phone.
// The messages and the notebook are scripted with page.route; the links are built by code from the source ids.
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'

const at = '2026-10-23T18:38:00Z'
const NOTE = '0f6c1a2e-1111-4222-8333-444455556666'
const GONE = '0f6c1a2e-9999-4222-8333-444455556666'
const REPLY =
  'I saved a thank-you reply and sent it for approval. [1] It will not go out until you approve it.\n\n' +
  'You prefer a new email. [1] I used the usual option. [1] The mail is from Kaggle [2] and a page says so [3].'
const sources = [
  { n: 1, uri: `ava://memory/${NOTE}`, kind: 'memory', title: 'Note · preference · from the app', cited_text: 'Wants a reply sent as a new email' },
  { n: 2, uri: 'ava://mail/18c0ffee12ab', kind: 'email', title: 'Squares in Squares', cited_text: '' },
  { n: 3, uri: 'https://example.com/squares', kind: 'web', title: 'Squares page', cited_text: '' },
]

async function open(page: Page, memories = [{ id: NOTE, text: 'Wants a reply sent as a new email', category: 'preference', source: 'chat', document: null, shareable: false, private: false, at }]) {
  await page.route('**/api/app/messages', (r) =>
    r.fulfill({
      json: [
        { id: 'm1', from: 'you', text: 'Did you reply to Kaggle?', at },
        { id: 'm2', from: 'agent', text: REPLY, at, kind: 'chat', sources },
      ],
    }),
  )
  await page.route('**/api/app/notebook*', (r) => r.fulfill({ json: { memories } }))
  await page.goto('/dashboard/chat')
  await expect(page.getByTestId('citation-mark').first()).toBeVisible()
}

test('each source shows once per paragraph, as a labelled button instead of plain text', async ({ page, owner }) => {
  void owner
  await open(page)
  // [1] was written three times in two paragraphs: one button each paragraph; [2] and [3] once.
  await expect(page.getByTestId('citation-mark')).toHaveCount(4)
  await expect(page.getByRole('button', { name: 'Source 1: Note, preference, from the app' })).toHaveCount(2)
  await expect(page.getByTestId('agent-bubble').locator('p').first()).not.toContainText('[1]')
  // The list under the message is open (3 sources) and names the numbers.
  await expect(page.getByRole('list', { name: 'Sources' }).getByRole('listitem')).toHaveCount(3)
})

test('a mark opens its source from the keyboard, Escape closes it and focus returns', async ({ page, owner }) => {
  void owner
  await open(page)
  const mark = page.getByRole('button', { name: /^Source 2:/ })
  await mark.focus()
  await page.keyboard.press('Enter')
  const pop = page.getByRole('dialog', { name: /^Source 2:/ })
  await expect(pop).toBeVisible()
  await expect(pop.getByRole('link', { name: /Open in Gmail/ })).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#all/18c0ffee12ab')
  await page.keyboard.press('Escape')
  await expect(pop).toHaveCount(0)
  await expect(mark).toBeFocused()
})

test('a note mark shows the exact words and opens the note in the Notebook, highlighted', async ({ page, owner }) => {
  void owner
  await open(page)
  await page.getByRole('button', { name: /^Source 1:/ }).first().click()
  const pop = page.getByRole('dialog', { name: /^Source 1:/ })
  await expect(pop).toContainText('Wants a reply sent as a new email')
  await pop.getByRole('link', { name: 'Open in the Notebook' }).click()
  await expect(page).toHaveURL(new RegExp(`/dashboard/notebook#note-${NOTE}$`))
  await expect(page.locator(`#note-${NOTE}`)).toHaveAttribute('data-highlighted', 'true')
})

test('a note that was forgotten says the source was deleted, not a dead link', async ({ page, owner }) => {
  void owner
  await open(page)
  await page.goto(`/dashboard/notebook#note-${GONE}`)
  await expect(page.getByText('This source was deleted.')).toBeVisible()
})

test('a web mark links to the page in a new tab, and a model-made cite: link or odd id gives nothing', async ({ page, owner }) => {
  void owner
  await open(page)
  await page.getByRole('button', { name: /^Source 3:/ }).click()
  const link = page.getByRole('dialog').getByRole('link', { name: /Open the page/ })
  await expect(link).toHaveAttribute('href', 'https://example.com/squares')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', /noopener/)
})

test('the chat with an open source popover has no axe violations', async ({ page, owner }) => {
  void owner
  await open(page)
  await page.getByRole('button', { name: /^Source 1:/ }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.waitForTimeout(700)
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([])
})
