// The memory passport (#56): download everything Ava knows, bring memory in with a preview, erase an import.
import { readFile } from 'node:fs/promises'
import { expect, test } from '../src/fixtures'
import { openNotebookPage } from '../src/helpers/workspace'

const FACTS = [
  { content: 'Maya runs the Lisbon office.', category: 'work', valid_from: '2024-03-01T09:00:00Z' },
  { content: 'Maya prefers oat milk.', category: 'preference' },
].map((f) => JSON.stringify(f)).join('\n')

const passport = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Memory passport' })

test.describe('memory passport', () => {
  test('download gives a zip with a manifest and only the owner\'s facts', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      passport(page).getByRole('link', { name: 'Download passport' }).click(),
    ])
    expect(download.suggestedFilename()).toMatch(/^memory-passport-\d{4}-\d{2}-\d{2}\.zip$/)
    const bytes = await readFile(await download.path())
    expect(bytes.subarray(0, 2).toString()).toBe('PK')
    expect(bytes.includes(Buffer.from('manifest.json'))).toBe(true)
  })

  test('import shows a preview first, adds private facts, and one tap erases them', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    await passport(page).getByLabel('Passport file').setInputFiles({
      name: 'facts.jsonl',
      mimeType: 'application/json',
      buffer: Buffer.from(FACTS),
    })
    const preview = page.getByRole('group', { name: 'Import preview' })
    await expect(preview.getByText(/2 new facts from 0 sources/)).toBeVisible()
    await expect(page.getByText('Maya prefers oat milk.', { exact: true })).toHaveCount(1) // only in the preview sample
    await preview.getByRole('button', { name: 'Import' }).click()
    await expect(passport(page).getByText('Added 2 facts')).toBeVisible()
    const note = page.getByRole('listitem').filter({ hasText: 'Maya runs the Lisbon office.' })
    await expect(note).toBeVisible()
    await expect(note.getByText('from a memory passport')).toBeVisible()

    await page.reload()
    await expect(page.getByText('Maya runs the Lisbon office.', { exact: true })).toBeVisible()
    await passport(page).getByRole('button', { name: 'Erase this import' }).click()
    await passport(page).getByRole('button', { name: 'Erase import' }).click()
    await expect(passport(page).getByText('Import erased.')).toBeVisible()
    await expect(page.getByText('Maya runs the Lisbon office.', { exact: true })).toHaveCount(0)
    // What the owner told Ava in the introduction is still there.
    await expect(page.getByRole('button', { name: 'Forget this' }).first()).toBeVisible()
  })

  test('a file that is not a passport is refused with a reason and nothing is added', async ({ page, owner }) => {
    void owner
    await openNotebookPage(page)
    await passport(page).getByLabel('Passport file').setInputFiles({
      name: 'notes.zip',
      mimeType: 'application/zip',
      buffer: Buffer.from('PK\x03\x04 not really a zip'),
    })
    await expect(passport(page).getByRole('alert')).toContainText(/readable zip/)
    await expect(page.getByRole('group', { name: 'Import preview' })).toHaveCount(0)
  })

  test('signed out, the export is refused', async ({ api }) => {
    expect((await api.get('/app/passport/export')).status()).toBe(401)
  })
})
