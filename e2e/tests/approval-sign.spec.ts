// Principal Card (#135): an email's approval card has a tick "Sign as Ava, personal assistant of <first name>",
// ticked by default; unticking sends it in the owner's own name; the choice is saved before the yes.
import { expect, query, test } from '../src/fixtures'
import { AGENT, queueEmail, TENANT } from './_sign-helper'

test('the email card is signed as Ava by default and the owner can untick it', async ({ page, owner }) => {
  void owner
  await queueEmail()
  await page.goto('/dashboard')
  const box = page.getByLabel('Sign as Ava, personal assistant of Jordan')
  await expect(box).toBeChecked()
  await expect(page.getByTestId('approval-sign-text')).toContainText('Ava, personal assistant of Jordan')
  await page.screenshot({ path: 'test-results/approval-sign-checked.png' })

  await box.uncheck()
  await expect(box).not.toBeChecked()
  await expect(page.getByTestId('approval-sign')).toContainText('Goes out in your own name')
  await page.reload()
  await expect(page.getByLabel('Sign as Ava, personal assistant of Jordan')).not.toBeChecked() // saved, not just shown
  await page.screenshot({ path: 'test-results/approval-sign-unchecked.png' })

  await page.getByLabel('Sign as Ava, personal assistant of Jordan').check()
  await expect(page.getByTestId('approval-sign-text')).toBeVisible()
})

test('a card with no sign-off to choose shows no tick', async ({ page, owner }) => {
  void owner
  await query(
    `INSERT INTO approval (id, tenant_id, agent_id, created_at, kind, payload, status)
     VALUES (gen_random_uuid(), $1, $2, now(), 'gmail_send', $3::jsonb, 'pending')`,
    [TENANT, AGENT, JSON.stringify({ summary: 'To: <a@b.example>', to: ['a@b.example'], subject: 'Hi' })],
  )
  await page.goto('/dashboard')
  await expect(page.getByTestId('approval-fields')).toBeVisible()
  await expect(page.getByTestId('approval-sign')).toHaveCount(0)
})
