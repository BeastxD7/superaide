// The Notebook shows everything Ava holds for you and stays true to it: a file saved but not yet read can be
// seen and erased after the introduction (#296), and a note another tab already forgot leaves the list once
// the error is shown (#298).
import { expect, query, test } from '../src/fixtures'

test.describe('the notebook stays in step', () => {
  test('a file saved but not read yet shows in the Notebook and can be erased', async ({ page, owner }) => {
    void owner // the introduction is finished, as in the issue
    const up = await page.request.post('/api/app/intro/files', {
      headers: { 'X-Requested-With': 'superaide-e2e' },
      multipart: { file: { name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('I lead applied AI in Singapore.') } },
    })
    expect(up.ok()).toBeTruthy()

    await page.goto('/dashboard/notebook')
    const section = page.getByRole('region', { name: 'Files and links not read yet' })
    await expect(section.getByText('notes.md')).toBeVisible()
    await expect(section.getByText('Saved, not read yet')).toBeVisible()

    await section.getByRole('button', { name: 'Erase notes.md', exact: true }).click()
    await section.getByRole('button', { name: 'Erase notes.md for good' }).click()
    await expect(page.getByText('Erased. Its text')).toBeVisible()
    await expect(section).toHaveCount(0)
    expect(await query('SELECT id FROM document')).toEqual([])
  })

  test('after "not found" the note leaves the list', async ({ page, context, owner }) => {
    void owner
    await page.goto('/dashboard/notebook')
    const firstNote = page.getByRole('listitem').filter({ has: page.getByRole('button', { name: 'Forget this' }) }).first()
    const text = (await firstNote.locator('p').first().innerText()).trim()
    const first = page.getByRole('listitem').filter({ hasText: text }).first()
    const other = await context.newPage()
    await other.goto('/dashboard/notebook')
    await expect(other.getByText(text).first()).toBeVisible()

    await first.getByRole('button', { name: 'Forget this' }).click()
    await first.getByRole('button', { name: 'Forget', exact: true }).click()
    await expect(page.getByText(text)).toHaveCount(0)

    const stale = other.getByRole('listitem').filter({ hasText: text }).first()
    await stale.getByRole('button', { name: 'Forget this' }).click()
    await stale.getByRole('button', { name: 'Forget', exact: true }).click()
    await expect(other.getByText(/Couldn.t forget that/)).toBeVisible()
    await expect(other.getByText(text)).toHaveCount(0)
  })
})
