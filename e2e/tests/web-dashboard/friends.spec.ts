// Friends (#479, #504): an account acknowledged as a contact's waits for the owner, who sees the platform's immutable
// id and approves it; accounts are listed for a contact; the invite says when Telegram is not connected.
import { expect, query, test } from '../../src/fixtures'
import { seedPendingMeera } from '../../src/helpers/contacts'

test('the owner sees the immutable id, approves, and the contact becomes a verified friend', async ({ page, api, owner }) => {
  void owner
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  const meera = await seedPendingMeera(me.person.id)
  await page.goto('/dashboard/people')
  const box = page.getByRole('region', { name: 'People' }).locator('section', { hasText: 'Waiting for your approval' })
  await expect(box).toContainText('Is this Meera Iyer?')
  await expect(box).toContainText('4242')
  await expect(box).toContainText('they chose this')
  await box.getByRole('button', { name: 'Yes, approve this account' }).click()
  await expect(page.getByText('Waiting for your approval')).toHaveCount(0)
  const rows = await query<{ person_id: string | null }>(`SELECT person_id FROM channel_identity WHERE stable_key = '4242'`)
  expect(rows[0].person_id).toBe(meera.personId)
  await page.goto(`/dashboard/people/${meera.contactId}`)
  await expect(page.getByText('Verified friend')).toBeVisible()
})

test('declining keeps a stranger; listing an account and the invite without Telegram', async ({ page, api, owner }) => {
  void owner
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  const meera = await seedPendingMeera(me.person.id)
  await page.goto('/dashboard/people')
  await page.getByRole('button', { name: 'No, keep as a stranger' }).click()
  await expect(page.getByText('Waiting for your approval')).toHaveCount(0)
  expect((await query<{ person_id: string | null }>(`SELECT person_id FROM channel_identity WHERE stable_key = '4242'`))[0].person_id).toBeNull()

  await page.goto(`/dashboard/people/${meera.contactId}`)
  await page.getByLabel('Username or id').fill('@meera_i2')
  await page.getByRole('button', { name: 'List account' }).click()
  await expect(page.getByText('Telegram @meera_i2')).toBeVisible()
  await page.getByLabel('Username or id').fill('no')
  await page.getByRole('button', { name: 'List account' }).click()
  await expect(page.getByText(/Enter a Telegram username/)).toBeVisible()
  await page.getByRole('button', { name: 'Make an invite link' }).click()
  await expect(page.getByText(/Connect Telegram first/)).toBeVisible()
})
