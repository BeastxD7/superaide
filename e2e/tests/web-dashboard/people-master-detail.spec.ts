// #663: People is a list with the chosen person beside it. The list stays while you move between people; on a phone the
// list is the page and a person opens on their own with a way back.
import { expect, test } from '../../src/fixtures'
import { seedMeera } from '../../src/helpers/contacts'

async function setup(api: import('../../src/fixtures').Api) {
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  return seedMeera(me.person.id)
}

test('at 1440 the list stays while a person is open beside it', async ({ page, api, owner }) => {
  void owner
  const meera = await setup(api)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/dashboard/people')
  const list = page.getByRole('list', { name: 'Your people' })
  await expect(page.getByText('Choose someone to see what Ava knows about them.')).toBeVisible()
  await list.getByRole('link', { name: /Meera Iyer/ }).click()
  await expect(page).toHaveURL(new RegExp(`/dashboard/people/${meera.contactId}$`))
  await expect(page.getByRole('heading', { level: 1, name: 'Meera Iyer' })).toBeVisible()
  await expect(list).toBeVisible() // still there beside the person
  await expect(list.getByRole('link', { name: /Meera Iyer/ })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('region', { name: 'Person' }).getByRole('link', { name: 'People', exact: true })).toBeHidden() // the Back link is for phones only
  const l = (await list.boundingBox())!
  const d = (await page.getByRole('heading', { level: 1, name: 'Meera Iyer' }).boundingBox())!
  expect(d.x).toBeGreaterThan(l.x + l.width) // the person is to the right of the list
  expect(await page.getByRole('heading', { level: 1 }).count()).toBe(1)
})

test('at 360 the list comes first, a person opens alone, and Back returns without sideways overflow', async ({ page, api, owner }) => {
  void owner
  await setup(api)
  await page.setViewportSize({ width: 360, height: 740 })
  await page.goto('/dashboard/people')
  const list = page.getByRole('list', { name: 'Your people' })
  await expect(list).toBeVisible()
  await list.getByRole('link', { name: /Meera Iyer/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Meera Iyer' })).toBeVisible()
  await expect(list).toBeHidden()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
  await page.getByRole('region', { name: 'Person' }).getByRole('link', { name: 'People', exact: true }).click()
  await expect(list).toBeVisible()
})
