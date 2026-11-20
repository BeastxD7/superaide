// The approval card on a phone: the identifier is readable and nothing runs off the side.
import { expect, test } from '../../src/fixtures'
import { seedPendingMeera } from '../../src/helpers/contacts'
import { expectNoSidewaysScroll } from '../../src/helpers/workspace'

test('the approval card fits a phone', async ({ page, api, owner }) => {
  void owner
  const me = (await (await api.get('/app/me')).json()) as { person: { id: string } }
  await seedPendingMeera(me.person.id)
  await page.goto('/dashboard/people')
  await expect(page.getByText('Is this Meera Iyer?')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Yes, approve this account' })).toBeVisible()
  await expectNoSidewaysScroll(page)
})
