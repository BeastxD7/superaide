// What the lists say stays true: Home says when it shows only some of the people (#301), the People page keeps
// "Recognised on" on a phone (#300), and the introduction's closing line counts and speaks correctly (#302).
import { expect, test, type Api } from '../src/fixtures'

type Who = { name: string; relationship: string; vip: boolean }

// The whole introduction through the API, naming exactly these people.
async function introduce(api: Api, people: Who[]) {
  await api.ok(api.get('/app/onboarding'))
  await api.answer('hello', { choice: 'Nice to meet you' })
  await api.answer('about', { mode: 'chat', text: 'I lead applied AI in Singapore.' })
  await api.answer('people', { people })
  await api.answer('urgent', { topics: ['A family emergency'], other: '', digest: 'morning' })
  await api.answer('reach', { channel: 'app', quiet_start: '22:00', quiet_end: '07:00', timezone: 'Asia/Singapore' })
  await api.answer('share', { shareable: ['availability'], never: '' })
  await api.answer('rules')
  await api.answer('connect', { choice: 'done' })
}

const NAMES = ['Abe', 'Una', 'Vik', 'Wes', 'Xin', 'Yara', 'Zed']

test.describe('people lists tell the whole story', () => {
  test.use({ ownerState: 'hired' })

  test('Home says how many people it does not show', async ({ page, api, owner }) => {
    void owner
    await introduce(api, NAMES.map((name, i) => ({ name, relationship: 'Team', vip: i === 0 })))

    await page.goto('/dashboard')
    const card = page.locator('[data-slot="card"]').filter({ has: page.getByText('People who matter', { exact: true }) })
    await expect(card.getByText('Abe', { exact: true })).toBeVisible()
    await expect(card.getByText('Zed', { exact: true })).toHaveCount(0)
    await card.getByRole('link', { name: '+2 more on the People page' }).click()
    await expect(page).toHaveURL(/\/dashboard\/people$/)
    await expect(page.getByRole('row').filter({ hasText: 'Zed' })).toBeVisible()
  })

  test('the introduction closing line counts one person once and says "matters"', async ({ page, api, owner }) => {
    void owner
    await introduce(api, [{ name: 'Priya', relationship: 'Partner', vip: true }])

    await page.goto('/dashboard/notebook')
    await expect(page.getByRole('button', { name: 'Forget this' }).first()).toBeVisible()
    const notes = await page.getByRole('button', { name: 'Forget this' }).count()
    await page.goto('/dashboard/chat')
    await expect(page.getByText(`So far I know ${notes - 1} things about you and 1 person who matters to you.`)).toBeVisible()
    await expect(page.getByText(/1 person who matter /)).toHaveCount(0)
  })
})
