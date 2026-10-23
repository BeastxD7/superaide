// The rehearsal room (#58): practise a hard conversation; Ava plays the other side; nothing is sent.
// The off-by-default checks run everywhere. The live flow is muted by default: it needs the fake Anthropic
// and credits (E2E_BRAIN=1) and the room switched on (E2E_REHEARSAL=1), for example:
//   E2E_BRAIN=1 E2E_REHEARSAL=1 E2E_SLOT=49 npx playwright test tests/rehearsal.spec.ts --project=desktop
import { REHEARSAL } from '../src/env'
import { expect, query, test } from '../src/fixtures'

test.describe('rehearsal room, switched off (the default)', () => {
  test.skip(REHEARSAL, 'only when the room is off')
  test.use({ ownerState: 'introduced' })

  test('there is no link, the page says it is off and the API answers 404', async ({ page, api, owner }) => {
    void owner
    await page.goto('/dashboard')
    await expect(page.getByRole('link', { name: 'Rehearsal room' })).toHaveCount(0)
    await page.goto('/dashboard/rehearsal')
    await expect(page.getByText('This isn’t turned on yet.')).toBeVisible()
    expect((await api.get('/app/rehearsal')).status()).toBe(404)
  })
})

test.describe('rehearsal room, switched on', () => {
  test.skip(!REHEARSAL || process.env.E2E_BRAIN !== '1', 'needs E2E_REHEARSAL=1 and E2E_BRAIN=1')
  test.use({ ownerState: 'hired' })

  test('practise, get a debrief that quotes you, then erase it', async ({ page, api, owner }) => {
    void owner
    await api.answer('brain', { choice: 'credits' })
    await api.completeIntroduction()
    const events = (await query<{ n: number }>('SELECT count(*)::int AS n FROM event'))[0].n
    const memories = (await query<{ n: number }>('SELECT count(*)::int AS n FROM memory_item'))[0].n

    await page.goto('/dashboard')
    await page.getByRole('link', { name: 'Rehearsal room' }).click()
    await expect(page.getByRole('heading', { name: 'Rehearsal room' })).toBeVisible()
    await page.getByLabel(/What should .* know about the situation/).fill('Goal: keep the team together.')
    await page.getByRole('button', { name: 'Start rehearsing' }).click()

    await expect(page.getByText('Rehearsal, not real')).toBeVisible()
    await page.getByLabel('Your line').fill('I need a raise before the end of the year')
    await page.getByRole('button', { name: 'Say it' }).click()
    await expect(page.getByText('That is a big ask.')).toBeVisible()

    await page.getByRole('button', { name: 'End and get my debrief' }).click()
    const debrief = page.getByLabel('Debrief')
    await expect(debrief.getByText('What worked')).toBeVisible()
    await expect(debrief.getByText(/You said: “I need a raise before the/).first()).toBeVisible()

    // Nothing was sent, remembered or stored as a message.
    expect((await query<{ n: number }>('SELECT count(*)::int AS n FROM event'))[0].n).toBe(events)
    expect((await query<{ n: number }>('SELECT count(*)::int AS n FROM memory_item'))[0].n).toBe(memories)

    await page.getByRole('button', { name: 'Erase it now' }).click()
    await expect(page.getByText('No rehearsals yet.')).toBeVisible()
    expect((await query<{ n: number }>('SELECT count(*)::int AS n FROM rehearsal_session'))[0].n).toBe(0)
  })
})
