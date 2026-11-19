// "While you were away" (#485): the briefing on Home lists who wrote with a suggested reply; Send is the owner's tap
// (one message to them), Dismiss sends nothing, and nothing shows when she has not been away.
import { ADMIN_TOKEN } from '../../src/env'
import { expect, query, test } from '../../src/fixtures'
import { Admin } from '../../src/helpers/workspace'
import { away, takenMessage } from './seed'

async function visitorSays(admin: Admin, text: string) {
  const r = await admin.post('/simulate', { provider: 'telegram', sender_id: '7001', sender_display: 'Meera Iyer', text })
  expect(r.ok(), `simulate → ${r.status()}`).toBeTruthy()
}

test.describe('while you were away', () => {
  test('the briefing lists the person with a suggested reply; Send goes through the relay checks', async ({ page, owner }) => {
    void owner
    const admin = new Admin(page.request, ADMIN_TOKEN)
    await visitorSays(admin, 'Hi, this is Meera. I need your answer about the project by 5pm.')
    await takenMessage('I need your answer about the project by 5pm.')
    await away()
    await page.goto('/dashboard')
    const card = page.getByTestId('briefing-card')
    await expect(card.getByText('While you were away')).toBeVisible()
    await expect(card.getByText('Meera', { exact: true })).toBeVisible()
    await expect(card.getByText('Written by someone Ava doesn’t know. Not verified.')).toBeVisible()
    await expect(card.getByText('I need your answer about the project by 5pm.')).toBeVisible()
    const draft = card.getByLabel(/Suggested reply to Meera/)
    await expect(draft).not.toHaveValue('')
    // The e2e stack has no Telegram connected, so the send is refused by the same checks as any relayed message: the
    // owner is told why, and the draft stays so she can try again.
    await card.getByRole('button', { name: 'Send the reply to Meera' }).click()
    await expect(card.getByText(/Not sent: Telegram is not connected/)).toBeVisible()
    await expect(draft).toBeVisible()
    const sent = await query<{ n: string }>(`SELECT count(*)::text AS n FROM approval WHERE kind = 'relay_message' AND status = 'approved'`)
    expect(sent[0].n).toBe('1') // one decision on record, with its result "Not sent: ..."
  })

  test('dismissing sends nothing, and there is no briefing when the owner was not away', async ({ page, owner }) => {
    void owner
    const admin = new Admin(page.request, ADMIN_TOKEN)
    await visitorSays(admin, 'Hi, this is Meera.')
    await takenMessage('Are you free to talk?')
    await page.goto('/dashboard')
    await expect(page.getByTestId('briefing-card')).toBeHidden() // she was here a moment ago
    await away()
    await page.reload()
    const card = page.getByTestId('briefing-card')
    await card.getByRole('button', { name: 'Dismiss the reply to Meera' }).click()
    await expect(card.getByText('No reply drafted.')).toBeVisible()
    const n = await query<{ n: string }>(`SELECT count(*)::text AS n FROM approval WHERE kind = 'relay_message'`)
    expect(n[0].n).toBe('0')
    await card.getByRole('button', { name: 'Got it' }).click()
    await expect(card).toBeHidden()
  })

  test('only the owner can read the briefing or send a draft', async ({ browser, owner }) => {
    void owner
    const anonymous = await browser.newContext()
    const id = '00000000-0000-0000-0000-000000000000'
    expect([401, 403]).toContain((await anonymous.request.get('/api/app/briefing')).status())
    const send = await anonymous.request.post(`/api/app/briefing/drafts/${id}/send`, { data: {}, headers: { 'X-Requested-With': 'superaide-e2e' } })
    expect([401, 403]).toContain(send.status())
    await anonymous.close()
  })
})
