// The owner's page for one person (#500): what Ava knows, where each fact came from and who may be told it; edit the
// owner's fields; forget a fact (a soft delete: the row stays, retired).
import { expect, query, test } from '../../src/fixtures'
import { seedMeera } from '../../src/helpers/contacts'

async function open(page: import('@playwright/test').Page, api: import('../../src/fixtures').Api) {
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  const meera = await seedMeera(me.person.id)
  await page.goto('/dashboard/people')
  await page.getByRole('link', { name: 'Meera Iyer' }).click()
  await expect(page).toHaveURL(new RegExp(`/dashboard/people/${meera.contactId}$`))
  return meera
}

test('the record shows facts with source, date and audience, and the owner edits and forgets', async ({ page, api, owner }) => {
  void owner
  await open(page, api)
  await expect(page.getByRole('heading', { level: 1, name: 'Meera Iyer' })).toBeVisible()
  await expect(page.getByText('@meera_i')).toBeVisible()
  const fact = page.getByRole('listitem').filter({ hasText: 'Meera prefers calls after 6pm.' })
  await expect(fact).toContainText('Told to Ava on Telegram')
  await expect(fact).toContainText('Shared with: Only you')

  await page.getByLabel('Your private notes').fill('Met at the offsite')
  await page.getByLabel('Relationship').fill('close friend')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Relationship')).toHaveValue('close friend')
  await expect(page.getByLabel('Your private notes')).toHaveValue('Met at the offsite')
  await page.getByLabel(/VIP: always gets through/).check()
  await expect.poll(async () => (await query<{ vip: boolean }>(`SELECT vip FROM contact`))[0].vip).toBe(true)

  await fact.getByRole('button', { name: 'Forget this' }).click()
  await fact.getByRole('button', { name: 'Forget' }).click()
  await expect(page.getByText('Meera prefers calls after 6pm.')).toHaveCount(0)
  const rows = await query<{ valid_to: Date | null }>(`SELECT valid_to FROM memory_item WHERE content LIKE 'Meera prefers%'`)
  expect(rows).toHaveLength(1) // soft delete: the row is kept
  expect(rows[0].valid_to).not.toBeNull()
})

test('an id that is not on the owner list says so instead of showing anything', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/people/6f1f5b8e-0000-4000-8000-000000000001')
  await expect(page.getByText(/Not found/i)).toBeVisible()
})
