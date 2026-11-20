// Tone of drafted replies (#683): visible in Settings, editable, and the owner's choice is kept.
import { expect, test } from '../../src/fixtures'

test.describe('tone of drafted replies', () => {
  test('the owner sees the tone, changes it, and can hand it back to be learned', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/settings')
    const card = page.getByTestId('tone-card')
    await expect(card.getByText('Tone of drafted replies')).toBeVisible()
    await expect(card.getByTestId('tone-source')).toHaveText('Nothing learned yet.')
    await card.getByRole('combobox').selectOption('formal')
    await expect(card.getByText('Saved. New drafts use this tone.')).toBeVisible()
    await expect(card.getByTestId('tone-source')).toHaveText('You chose this. Ava will not change it.')
    await page.reload()
    await expect(page.getByTestId('tone-card').getByRole('combobox')).toHaveValue('formal')
    await page.getByTestId('tone-card').getByRole('button', { name: /learn it again/ }).click()
    await expect(page.getByTestId('tone-source')).not.toHaveText('You chose this. Ava will not change it.')
  })

  test('a signed-out visitor cannot read or change it', async ({ browser, owner }) => {
    void owner
    const anonymous = await browser.newContext()
    expect([401, 403]).toContain((await anonymous.request.get('/api/app/briefing/tone')).status())
    const put = await anonymous.request.put('/api/app/briefing/tone', { data: { tone: 'warm' }, headers: { 'X-Requested-With': 'superaide-e2e' } })
    expect([401, 403]).toContain(put.status())
    await anonymous.close()
  })
})
