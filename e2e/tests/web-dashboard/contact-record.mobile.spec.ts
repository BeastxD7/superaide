// The person's record on a phone: nothing runs off the side and the actions can be reached.
import { expect, test } from '../../src/fixtures'
import { seedMeera } from '../../src/helpers/contacts'
import { expectNoSidewaysScroll } from '../../src/helpers/workspace'

test('a person\'s record fits a phone', async ({ page, api, owner }) => {
  void owner
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  const meera = await seedMeera(me.person.id)
  await page.goto(`/dashboard/people/${meera.contactId}`)
  await expect(page.getByRole('heading', { level: 1, name: 'Meera Iyer' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Forget this' })).toBeVisible()
  await expectNoSidewaysScroll(page)
  await page.screenshot({ path: 'test-results/contact-mobile.png', fullPage: true })
})
