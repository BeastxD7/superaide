// #411: how long a new owner takes from the app being reachable to a first useful reply from Ava, on a fresh database.
// Numbers only. Opt-in and nightly (not on pull requests); it needs the hosted stack with a fake Anthropic:
//   E2E_TTFR=1 E2E_BRAIN=1 E2E_SLOT=73 npx playwright test --project=ttfr
// Stages (docs/onboarding-time.md): start, app_reachable, sign_up, hire, brain_connected, introduction_done, first_reply. The person is a
// script, so it never reads or hesitates: the total is a floor that the product's own speed sets, not a human's time.
import { expect, newPerson, test } from '../../src/fixtures'
import { Stopwatch, writeResult } from '../../src/helpers/stopwatch'
import { Meet } from '../../src/helpers/introduction'
import { composer } from '../../src/helpers/workspace'

test.skip(process.env.E2E_TTFR !== '1' || process.env.E2E_BRAIN !== '1', 'needs E2E_TTFR=1 and E2E_BRAIN=1 (hosted stack, fake Anthropic)')

const HOSTED_TARGET_SECONDS = 180

test('a new owner goes from the landing page to a first useful reply', async ({ page, api }) => {
  void api // a fresh database
  test.setTimeout(180_000)
  const me = newPerson('Ananya Menon')
  const sw = new Stopwatch()

  await page.goto('/')
  await expect(page.getByRole('link', { name: 'Meet Ava' }).first()).toBeVisible()
  sw.mark('app_reachable')

  await page.getByRole('link', { name: 'Meet Ava' }).first().click()
  await page.getByRole('button', { name: 'Hire Ava' }).click()
  await page.waitForLoadState('networkidle') // the form is interactive (hydrated): a script is faster than a person here
  await page.getByLabel('Your name').fill(me.name)
  await page.getByLabel('Email').fill(me.email)
  await page.getByLabel('Password', { exact: true }).fill(me.password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page).toHaveURL(/\/agents\/ava\/hire$/)
  sw.mark('sign_up')

  await expect(page.getByText('Ava is an AI personal assistant.')).toBeVisible()
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Hire Ava' }).click()
  await expect(page.getByRole('heading', { name: 'Ava is on your team' })).toBeVisible()
  sw.mark('hire')

  await page.getByRole('button', { name: /Say hello/ }).click()
  await expect(page).toHaveURL(/\/meet$/)
  await page.getByRole('group', { name: /Choose .*'s brain/ }).getByRole('button', { name: 'Use Superaide AI credits' }).click()
  await expect(page.getByText(/Step 2 of 9: Hello/)).toBeVisible()
  sw.mark('brain_connected')

  // The dashboard opens once the introduction is done (Ava's own scripted questions: no model call, but her typing pauses count).
  const meet = new Meet(page)
  // With the brain step in front the introduction has 9 steps, so wait on each step's own control, not its number.
  await meet.chip('Nice to meet you').click()
  await meet.tab('Tell me here').click()
  await meet.aboutBox().fill('I lead applied AI at Larkfield in Singapore.')
  await meet.send().click()
  await meet.personName(1).fill('Priya Tan')
  await meet.peopleSubmit().click()
  await meet.urgentSubmit().click()
  await meet.reachSubmit().click()
  await meet.shareSubmit().click()
  await meet.rulesSubmit().click()
  await meet.finish().click()
  await meet.startWorking().click()
  await expect(page).toHaveURL(/\/dashboard/)
  sw.mark('introduction_done')

  await page.goto('/dashboard/chat')
  await composer(page).fill('What can you do for me this week?')
  await composer(page).press('Enter')
  await expect(page.getByText('Hello from the fake Claude.').first()).toBeVisible({ timeout: 30_000 })
  sw.mark('first_reply')

  writeResult(`test-results/ttfr/journey.json`, sw, { mode: 'hosted', target: { name: 'hosted', seconds: HOSTED_TARGET_SECONDS } })
  console.log(`TTFR hosted journey: ${sw.marks.map((m) => `${m.stage}=${m.at_ms}ms`).join(' ')}`)
  expect(sw.total_ms, `slower than the ${HOSTED_TARGET_SECONDS}s target`).toBeLessThan(HOSTED_TARGET_SECONDS * 1000)
})
