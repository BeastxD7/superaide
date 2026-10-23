// Hiring Ava and the introduction by chat:
//   hire → hello → about (tell me here) → people → urgent → reach → share → rules → connect → done → dashboard
// Each step is tested for what it accepts and for what it refuses (with the hint a person sees),
// plus coming back later (reload, leave, new tab, log out and in) and double submission.
import { existsSync } from 'node:fs'
import { resetDatabase } from '../src/db'
import { BASE_URL } from '../src/env'
import { expect, newPerson, query, test } from '../src/fixtures'
import {
  AVA,
  HirePage,
  Meet,
  contacts,
  logOut,
  openNotebook,
  eventCount,
  memories,
  onboardingStep,
  preference,
  totalEvents,
  trackPosts,
} from '../src/helpers/introduction'

// Ava "types" each line for up to two seconds. Most tests ask for reduced motion (a real
// browser setting) so her lines appear at once; the full walk-through keeps the animation.
// A fixed time zone lets us check the one the browser reports is the one saved.
test.use({ contextOptions: { reducedMotion: 'reduce' }, timezoneId: 'Asia/Tokyo' })

const first = (name: string) => name.split(' ')[0]
const AVA_URGENT_LINE = "What counts as urgent for you? I'll only interrupt you for these. Everything else goes in a digest."

async function hires(): Promise<{ owner_user_id: string; status: string; kind: string; version: string }[]> {
  return query(
    "SELECT owner_user_id::text, status, notice->>'kind' AS kind, notice->>'version' AS version FROM agent_hire",
  )
}

// ----------------------------------------------------------------------------------
test.describe('hiring Ava', () => {
  test.use({ ownerState: 'account' })

  test('the notice must be accepted before Hire works; hiring lands on "Ava is on your team" @smoke', async ({ page, owner }) => {
    const hire = new HirePage(page)
    await hire.open()
    await expect(hire.agree()).not.toBeChecked()
    await expect(hire.hireButton()).toBeDisabled()

    // Ticking and unticking the box turns the button on and off again.
    await hire.agree().check()
    await expect(hire.hireButton()).toBeEnabled()
    await hire.agree().uncheck()
    await expect(hire.hireButton()).toBeDisabled()
    expect(await hires()).toHaveLength(0)

    await hire.agree().check()
    await hire.hireButton().click()
    await expect(hire.hired()).toBeVisible()

    // What was agreed is kept with the hire.
    const rows = await hires()
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ status: 'active', kind: 'consent_at_hire' })
    expect(rows[0].version).toMatch(/^\d{4}-\d{2}-\d{2}$/)

    // "Say hello" starts the introduction with Ava's greeting.
    await hire.sayHello().click()
    await expect(page).toHaveURL(/\/meet$/)
    const meet = new Meet(page)
    await meet.expectStep('Hello')
    await expect(meet.line(AVA.hello(first(owner.name)))).toBeVisible()
    await expect(meet.chip('Nice to meet you')).toBeVisible()
    expect(await onboardingStep()).toBe('hello')

    // The back button doesn't offer to hire her again: the hire page sends you on.
    await page.goBack()
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Hello')
    expect(await hires()).toHaveLength(1)
  })

  test('keyboard only: tick the notice and hire with Space and Enter', async ({ page, owner }) => {
    void owner
    const hire = new HirePage(page)
    await hire.open()
    await hire.agree().focus()
    await page.keyboard.press('Space')
    await expect(hire.agree()).toBeChecked()
    await page.keyboard.press('Tab')
    await expect(hire.hireButton()).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(hire.hired()).toBeVisible()
    await hire.sayHello().focus()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/meet$/)
  })

  test('the API refuses a hire without the notice, for an unknown agent, signed out, or without the CSRF header @smoke', async ({ page, api, owner }) => {
    void owner
    const noNotice = await api.post('/app/hire', { agent: 'ava', accepted_notice: false })
    expect(noNotice.status()).toBe(400)
    expect((await noNotice.json()).detail).toMatch(/accept how the agent handles/)

    expect((await api.post('/app/hire', { agent: 'ava' })).status()).toBe(422)
    expect((await api.post('/app/hire', { agent: 'nobody', accepted_notice: true })).status()).toBe(404)
    expect((await api.postWithoutCsrfHeader('/app/hire', { agent: 'ava', accepted_notice: true })).status()).toBe(403)
    // Not hired yet: nothing after hiring is open to this account.
    expect((await api.get('/app/onboarding')).status()).toBe(403)

    await page.context().clearCookies()
    expect((await api.post('/app/hire', { agent: 'ava', accepted_notice: true })).status()).toBe(401)
    expect(await hires()).toHaveLength(0)
  })

  test('when hiring fails, the reason is shown and you can try again', async ({ page, owner }) => {
    void owner
    const hire = new HirePage(page)
    await hire.open()
    await page.route('**/api/app/hire', (route) =>
      route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Superaide is restarting. Try again in a minute.' }) }),
    )
    await hire.agree().check()
    await hire.hireButton().click()
    await expect(page.getByText('Superaide is restarting. Try again in a minute.')).toBeVisible()
    await expect(hire.hired()).toBeHidden()
    await expect(hire.hireButton()).toBeEnabled()
    expect(await hires()).toHaveLength(0)

    await page.unroute('**/api/app/hire')
    await hire.hireButton().click()
    await expect(hire.hired()).toBeVisible()
    expect(await hires()).toHaveLength(1)
  })

  test('double-clicking Hire hires once and greets once', async ({ page, owner }) => {
    const posts = trackPosts(page, '/api/app/hire')
    const hire = new HirePage(page)
    await hire.open()
    await hire.agree().check()
    await hire.hireButton().dblclick()
    await expect(hire.hired()).toBeVisible()
    await posts.allFinished()
    await expect(page.getByText(/hired by someone else|already works for/)).toBeHidden()
    expect(await hires()).toHaveLength(1)
    expect(await eventCount(AVA.hello(first(owner.name)), 'out')).toBe(1)
  })

  test('hire requests sent at the same moment by the same owner all succeed', async ({ page, api }) => {
    test.fail(true, 'BUG: overlapping hires by the same owner: all but one get 409 "Ava was just hired by someone else."')
    // The requests race, so try a few fresh installs; any refusal shows the bug.
    let refused: string[] = []
    for (let round = 0; round < 4 && refused.length === 0; round++) {
      await resetDatabase()
      await page.context().clearCookies()
      await api.signup(newPerson())
      const responses = await Promise.all(Array.from({ length: 5 }, () => api.post('/app/hire', { agent: 'ava', accepted_notice: true })))
      expect(await hires()).toHaveLength(1)
      refused = await Promise.all(responses.filter((r) => !r.ok()).map(async (r) => `${r.status()} ${await r.text()}`))
    }
    expect(refused).toEqual([])
  })

  test('a second account hires its own Ava and never sees the first owner (UI and API) @smoke', async ({ page, api }) => {
    // Many owners is the default (ADR 0009): Ava is never "taken".
    const meera = newPerson('Meera Iyer')
    await api.signup(meera)
    await api.hire()
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meeraId = (await hires())[0].owner_user_id

    await page.context().clearCookies()
    const rahul = newPerson('Rahul Verma')
    await api.signup(rahul)

    // The profile offers Ava to Rahul too, and never names who else hired her.
    await page.goto('/agents/ava')
    await expect(page.getByText('Available now', { exact: true })).toBeVisible()
    await expect(page.getByText(/Meera/)).toHaveCount(0)
    await expect(page.getByText('Already hired', { exact: true })).toHaveCount(0)

    const hire = new HirePage(page)
    await hire.open()
    await hire.agree().check()
    await hire.hireButton().click()
    await expect(hire.hired()).toBeVisible()

    // Two hires, two owners; Rahul's introduction starts fresh, with none of Meera's.
    const rows = await hires()
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.owner_user_id)).toContain(meeraId)
    const me = await api.ok<{ person: { name: string }; onboarding: { step: string } }>(api.get('/app/me'))
    expect(me.person.name).toBe('Rahul Verma')
    expect(me.onboarding.step).toBe('hello')
    expect(JSON.stringify(await api.ok(api.get('/app/messages')))).not.toContain('Meera')
  })
})

test.describe('hiring again', () => {
  test.use({ ownerState: 'hired' })

  test('the same owner hiring again is harmless: same person, same step, nothing repeated', async ({ page, api, owner }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI at PebbleRoad.' })
    const before = await totalEvents()
    const person = (await query<{ id: string }>('SELECT person_id::text AS id FROM onboarding'))[0].id

    const again = await api.hire()
    expect(again).toMatchObject({ hired: 'ava', person: { id: person, name: owner.name } })
    expect(await hires()).toHaveLength(1)
    expect(await onboardingStep()).toBe('people')
    expect(await totalEvents()).toBe(before)

    // The hire page sends you on to where you left off; the profile says she's yours.
    await page.goto('/agents/ava/hire')
    await expect(page).toHaveURL(/\/meet$/)
    await new Meet(page).expectStep('Your people')
    await page.goto('/agents/ava')
    await expect(page.getByText('Works for you')).toBeVisible()
    await page.getByRole('button', { name: 'Open Ava' }).click()
    await expect(page).toHaveURL(/\/meet$/)
  })
})

// ----------------------------------------------------------------------------------
test.describe('the whole introduction, as a person sees it @smoke', () => {
  // Ava types each line, with the animation on.
  test.use({ ownerState: 'hired', contextOptions: { reducedMotion: 'no-preference' } })

  test('hello → about → people → urgent → reach → share → rules → connect → done → dashboard, saved in the notebook and the database', async ({ page, owner }) => {
    test.slow()
    const name = first(owner.name)
    const meet = new Meet(page)
    await meet.open()
    await expect(page.getByText(`You hired Ava today`)).toBeVisible()

    // Hello
    await meet.expectStep('Hello')
    await expect(meet.line(AVA.hello(name))).toBeVisible()
    await meet.chip('What will you do for me?').click()
    await expect(meet.line(/^As I learn, I move up: Child, Adolescent, then Adult/)).toBeVisible()

    // About you, told here
    await meet.expectStep('About you')
    await expect(meet.line(new RegExp(`^${AVA.about}`))).toBeVisible()
    await expect(meet.tab('Tell me here')).toHaveAttribute('aria-selected', 'true')
    const about = 'I lead applied AI at PebbleRoad in Singapore. Mornings are for client work.'
    await meet.aboutBox().fill(about)
    await meet.send().click()
    await expect(meet.line(about)).toBeVisible()
    await expect(meet.line(`Thanks, ${name}. I've noted that.`)).toBeVisible()

    // Your people
    await meet.expectStep('Your people')
    await meet.people([
      { name: 'Priya', relationship: 'Partner', vip: true },
      { name: 'Arjun', relationship: 'Team', vip: false },
    ])
    await expect(meet.line('Got it: Priya and Arjun. Priya can always get through.')).toBeVisible()

    // Urgent: two of my own picks and one written in, digest every morning
    await expect(meet.urgentChip('A family emergency')).toHaveAttribute('aria-pressed', 'true')
    await meet.urgentChip('Anything from the people who can always get through').click()
    await meet.urgentChip('Travel changes').click()
    await meet.urgentOther().fill('Anything about the Series A')
    await meet.digest('Every morning').click()
    await expect(meet.digest('Every morning')).toHaveAttribute('aria-pressed', 'true')
    await meet.urgent()
    await expect(
      meet.line('Urgent: A family emergency; Travel changes; Anything about the Series A. Digest: every morning.'),
    ).toBeVisible()

    // Reaching you: quiet 22:30–06:45, people who always get through do not break it
    await expect(page.getByText('Asia/Tokyo')).toBeVisible()
    await expect(page.getByRole('button', { name: /Telegram/ })).toBeDisabled()
    await meet.quietStart().fill('22:30')
    await meet.quietEnd().fill('06:45')
    await page.getByRole('switch', { name: 'Let the people who can always get through break quiet hours' }).click()
    await meet.reach()
    await expect(meet.line('Reach me here in the app. Quiet hours 22:30 to 06:45.')).toBeVisible()

    // Sharing
    await expect(meet.shareToggle("Whether you're free or busy, without details")).toHaveAttribute('aria-checked', 'true')
    await meet.shareToggle("That you're in a meeting or travelling").click()
    await meet.shareToggle('Your work email').click()
    await meet.neverShare().fill('My home address and my kids’ school')
    await meet.share()
    await expect(
      meet.line("Whether you're free or busy, without details; Your work email. Never share: My home address and my kids’ school"),
    ).toBeVisible()

    // Ground rules, connect, done
    await expect(page.getByText("I'm an AI, and I'll always say so to anyone I talk to.")).toBeVisible()
    await meet.rules()
    await expect(meet.line(new RegExp(`^${AVA.connect}`))).toBeVisible()
    await meet.connect()
    await expect(meet.line(new RegExp(`^${AVA.done(name).replace('.', '\\.')}`))).toBeVisible()
    await expect(meet.line(/^So far I know \d+ things about you and 2 people who matter to you\./)).toBeVisible()
    await expect(meet.line(/I'll interrupt you for: a family emergency, travel changes and anything about the series a\./i)).toBeVisible()

    // "Start working with Ava" opens the dashboard, and the notebook shows what I said
    await meet.startWorking().click()
    await expect(page).toHaveURL(/\/dashboard$/)
    const notebook = await openNotebook(page)
    for (const fact of [
      `${owner.name} says: ${about}`,
      `Priya is ${owner.name}'s partner; they can always get through, even when busy.`,
      `Arjun is ${owner.name}'s team.`,
      `${owner.name} wants to be interrupted only for: A family emergency; Travel changes; Anything about the Series A.`,
      `${owner.name}'s quiet hours are 22:30–06:45 (Asia/Tokyo); urgent messages go here in the app.`,
      `${owner.name} asked that this is never shared with anyone: My home address and my kids’ school`,
    ]) {
      await expect(notebook.getByText(fact, { exact: true })).toBeVisible()
    }

    // …and the database has the same.
    expect(await contacts()).toEqual([
      { name: 'Arjun', relationship: 'team', vip: false },
      { name: 'Priya', relationship: 'partner', vip: true },
    ])
    expect(await preference()).toEqual({
      urgent_topics: ['A family emergency', 'Travel changes', 'Anything about the Series A'],
      digest: 'morning',
      interrupt_channel: 'app',
      quiet_start: '22:30',
      quiet_end: '06:45',
      vip_breaks_quiet: false,
      timezone: 'Asia/Tokyo',
      shareable: ['availability', 'work_email'],
      never_share: 'My home address and my kids’ school',
    })
    const state = await query<{ step: string; done: boolean; rules: boolean }>(
      "SELECT step, completed_at IS NOT NULL AS done, answers ? 'rules_accepted_at' AS rules FROM onboarding",
    )
    expect(state).toEqual([{ step: 'done', done: true, rules: true }])
    const audit = await query<{ action: string }>(
      "SELECT action FROM audit_log WHERE action IN ('hired', 'ground_rules_accepted', 'onboarding_completed') ORDER BY created_at",
    )
    expect(audit.map((a) => a.action)).toEqual(['hired', 'ground_rules_accepted', 'onboarding_completed'])

    // Each of Ava's lines was said once.
    for (const line of [AVA.hello(name), `Thanks, ${name}. I've noted that.`]) expect(await eventCount(line, 'out')).toBe(1)
  })
})

test.describe('the introduction by chat', () => {
  test.use({ ownerState: 'hired' })
  test.beforeEach(({ owner }) => void owner) // signed up and hired before each test

  test('keyboard: Enter sends the introduction and Shift+Enter starts a new line', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('About you')
    await expect(meet.aboutBox()).toBeFocused() // the composer is ready to type into
    await page.keyboard.type('I lead applied AI at PebbleRoad.')
    await page.keyboard.press('Shift+Enter')
    await page.keyboard.type('Most of my week is client work.')
    await page.keyboard.press('Enter')
    await meet.expectStep('Your people')
    expect(await eventCount('I lead applied AI at PebbleRoad.\nMost of my week is client work.', 'in')).toBe(1)
  })

  test('skipping people and sharing still finishes, with nothing shared', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.peopleSkip().click()
    await meet.expectStep('Urgent')
    await expect(meet.line('No problem. You can add people any time from your notebook.')).toBeVisible()
    await meet.urgent()
    await meet.reach()
    await meet.shareSkip().click()
    await meet.expectStep('Ground rules')
    await expect(meet.line('Nothing for now')).toBeVisible()
    await meet.rules()
    await meet.connect()
    expect(await contacts()).toEqual([])
    expect((await preference())?.shareable).toEqual([])
  })
})

// ----------------------------------------------------------------------------------
test.describe('each step refuses what it should, and says why', () => {
  test.use({ ownerState: 'hired' })
  test.beforeEach(({ owner }) => void owner) // signed up and hired before each test

  test('about: empty or blank text cannot be sent; the API asks for at least one line', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('About you')
    await expect(meet.send()).toBeDisabled()
    await meet.aboutBox().fill('   \n   ')
    await expect(meet.send()).toBeDisabled()
    await meet.aboutBox().press('Enter')
    await meet.expectStep('About you')

    // "Continue" after files and links needs something shared first.
    await meet.tab('Files & links').click()
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
    await meet.tab('Video call').click()
    await expect(page.getByRole('button', { name: "I'm done introducing myself" })).toHaveCount(0)

    for (const text of ['', '   ', '\n\t']) {
      const v = await api.answer('about', { mode: 'chat', text })
      expect(v).toMatchObject({ step: 'about', hint: 'Even one line helps. What do you do?' })
    }
    expect(await api.answer('about', { mode: 'continue' })).toMatchObject({
      step: 'about',
      hint: 'Share at least one thing about yourself first.',
    })
    expect(await onboardingStep()).toBe('about')
    expect(await memories()).toEqual([])
    expect(await eventCount('', 'in')).toBe(0)
  })

  test('people: blank and whitespace-only names are not sent; names are tidied', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Your people')
    await expect(meet.peopleSubmit()).toBeDisabled() // two empty rows
    await meet.personName(1).fill('     ')
    await meet.personName(2).fill('\t')
    await expect(meet.peopleSubmit()).toBeDisabled()

    // A real name in the second row; the blank first row is left out.
    await meet.personName(2).fill('  Priya    Sharma  ')
    await expect(meet.peopleSubmit()).toBeEnabled()
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    await expect(meet.line('Got it: Priya Sharma.')).toBeVisible()
    expect(await contacts()).toEqual([{ name: 'Priya Sharma', relationship: 'family', vip: false }])
  })

  test('people: the same name twice becomes one contact', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.personName(1).fill('Priya')
    await meet.personRelationship(1).selectOption('Partner')
    await meet.personName(2).fill('priya')
    await meet.personRelationship(2).selectOption('Partner')
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    const saved = await contacts()
    expect(saved).toHaveLength(1)
    expect(saved[0]).toMatchObject({ relationship: 'partner', vip: true })
  })

  test('people: Ava does not repeat a duplicated name back', async ({ page, api }) => {
    // I9, fixed: app/onboarding.py skips a name already given in the same answer, so Ava says it once.
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.personName(1).fill('Priya')
    await meet.personName(2).fill('Priya')
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    await expect(meet.line(/^Got it: Priya\. Priya can always get through\.$/)).toBeVisible()
  })

  test('people: a name containing % is its own person, not merged into someone else', async ({ page, api }) => {
    // I5, fixed: knowledge/store.py matches contacts by case-insensitive equality, not ILIKE.
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.people([
      { name: 'Arjun', relationship: 'Team', vip: false },
      { name: 'A%', relationship: 'Friend', vip: false },
    ])
    const saved = await contacts()
    expect(saved).toHaveLength(2)
    expect(saved).toEqual(
      expect.arrayContaining([
        { name: 'Arjun', relationship: 'team', vip: false },
        { name: 'A%', relationship: 'friend', vip: false },
      ]),
    )
  })

  test('people: at most ten rows, and very long names are cut, not refused', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    for (let i = 3; i <= 10; i++) await meet.addSomeone().click()
    await expect(meet.personName(10)).toBeVisible()
    await expect(meet.addSomeone()).toHaveCount(0)
    await meet.removePerson(10).click()
    await expect(meet.addSomeone()).toBeVisible()

    const long = 'Priya' + 'a'.repeat(295)
    await meet.personName(1).fill(long)
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    const saved = await contacts()
    expect(saved).toHaveLength(1)
    expect(saved[0].name).toBe(long.slice(0, 120))
  })

  test('urgent: with nothing chosen the button stays off; the API asks for at least one', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Urgent')
    // Two are picked to start with; turn both off.
    for (const o of ['Anything from the people who can always get through', 'A family emergency']) {
      await expect(meet.urgentChip(o)).toHaveAttribute('aria-pressed', 'true')
      await meet.urgentChip(o).click()
      await expect(meet.urgentChip(o)).toHaveAttribute('aria-pressed', 'false')
    }
    await expect(meet.urgentSubmit()).toBeDisabled()
    await meet.urgentOther().fill('    ')
    await expect(meet.urgentSubmit()).toBeDisabled()
    await meet.urgentOther().fill('Anything about the Series A')
    await expect(meet.urgentSubmit()).toBeEnabled()
    await meet.urgentOther().fill('')
    await expect(meet.urgentSubmit()).toBeDisabled()

    for (const answer of [{ topics: [] }, { topics: ['  ', ''], other: '   ' }, {}]) {
      expect(await api.answer('urgent', answer)).toMatchObject({ step: 'urgent', hint: 'Pick at least one, or write your own.' })
    }
    expect(await onboardingStep()).toBe('urgent')
  })

  test('urgent: the API does not split a topic sent as text into letters', async ({ api }) => {
    // I11, fixed: app/onboarding.py's _list() reads anything but a list as [].
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: 'Travel', other: '' })
    expect((await preference())?.urgent_topics ?? []).not.toEqual(['T', 'r', 'a', 'v', 'e', 'l'])
  })

  test('reach: a missing or impossible time is refused with a hint, and nothing is saved', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Reaching you')
    await expect(meet.quietStart()).toHaveValue('22:00')
    await expect(meet.quietEnd()).toHaveValue('07:00')

    // Clearing a time and saving: Ava stays on this step and says what to do.
    await meet.quietStart().fill('')
    await meet.reachSubmit().click()
    await expect(page.getByText('Use times like 22:00 and 07:00.')).toBeVisible()
    await meet.expectStep('Reaching you')
    expect(await onboardingStep()).toBe('reach')

    // The API refuses times a time picker can't make.
    for (const [start, end] of [['25:00', '07:00'], ['22:00', '24:00'], ['9:00', '07:00'], ['12:60', '07:00'], ['22:00', ''], [' 22:00', '07:00'], ['22:00:00', '07:00']]) {
      const v = await api.answer('reach', { quiet_start: start, quiet_end: end })
      expect(v, `${start}–${end}`).toMatchObject({ step: 'reach', hint: 'Use times like 22:00 and 07:00.' })
    }
    expect(await preference()).toMatchObject({ quiet_start: '22:00', quiet_end: '07:00' })

    // Fixing the time moves on.
    await meet.quietStart().fill('23:15')
    await meet.reach()
    expect(await preference()).toMatchObject({ quiet_start: '23:15', quiet_end: '07:00', timezone: 'Asia/Tokyo' })
  })

  test('the hint for a refused answer is announced to screen readers', async ({ page, api }) => {
    test.fail(true, 'BUG: hints and error notices are plain text, with no role="status" or "alert", so screen readers stay silent')
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    const meet = new Meet(page)
    await meet.open()
    await meet.quietStart().fill('')
    await meet.reachSubmit().click()
    const hint = 'Use times like 22:00 and 07:00.'
    await expect(page.getByText(hint)).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: hint }).or(page.getByRole('alert').filter({ hasText: hint }))).toBeVisible()
  })

  test('reach: the same start and end time is refused', async ({ page, api }) => {
    test.fail(true, 'BUG: quiet hours 09:00 to 09:00 are accepted; they should be refused (or explained)')
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    const posts = trackPosts(page, '/api/app/onboarding')
    const meet = new Meet(page)
    await meet.open()
    await meet.quietStart().fill('09:00')
    await meet.quietEnd().fill('09:00')
    await meet.reachSubmit().click()
    expect(await posts.allFinished()).toBe(1)
    expect(await onboardingStep()).toBe('reach')
    await meet.expectStep('Reaching you')
    await expect(meet.reachSubmit()).toBeVisible()
  })

  test('reach: an Indian time zone, which Chrome reports as Asia/Calcutta, is saved rather than swapped for Singapore', async ({ page, browser, api }) => {
    // Still open in the code (no alias mapping; tzdata is only a dependency on Windows). It passes
    // only where the system zoneinfo has the legacy name (macOS does), so it is a known bug elsewhere.
    test.fail(
      !existsSync('/usr/share/zoneinfo/Asia/Calcutta'),
      'BUG: the API rejects legacy zone names (Asia/Calcutta) on servers without tzdata backward links and silently saves Asia/Singapore',
    )
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    // A browser in India, signed in as the same owner.
    const context = await browser.newContext({
      baseURL: BASE_URL,
      timezoneId: 'Asia/Kolkata',
      reducedMotion: 'reduce',
      storageState: await page.context().storageState(),
    })
    const india = await context.newPage()
    const meet = new Meet(india)
    await meet.open()
    await meet.expectStep('Reaching you')
    const reported = await india.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)
    await expect(india.getByText(reported, { exact: true })).toBeVisible()
    await meet.reach()
    await context.close()
    expect(['Asia/Kolkata', 'Asia/Calcutta']).toContain((await preference())?.timezone)
  })

  test('reach: an unknown time zone or an unconnected channel falls back safely', async ({ api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    // An unknown zone keeps the one already stored (the browser's zone from sign-up, UTC if none).
    const timezone = (await preference())?.timezone
    const v = await api.answer('reach', { channel: 'telegram', quiet_start: '21:00', quiet_end: '06:00', timezone: 'Mars/Olympus_Mons' })
    expect(v.step).toBe('share')
    expect(await preference()).toMatchObject({ interrupt_channel: 'app', timezone, quiet_start: '21:00' })
  })

  test('share: never-share text with HTML or a script is shown as text and never runs', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    await api.answer('reach', { quiet_start: '22:00', quiet_end: '07:00' })
    let dialogs = 0
    page.on('dialog', (d) => {
      dialogs += 1
      void d.dismiss()
    })
    const hostile = `<img src=x onerror="window.__pwned=1;alert(1)"><script>window.__pwned=2</script><b>bold</b> & "quotes"`
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Sharing')
    await meet.neverShare().fill(hostile)
    await meet.share()
    await expect(meet.line(`Whether you're free or busy, without details; That you're in a meeting or travelling. Never share: ${hostile}`)).toBeVisible()
    await expect(meet.conversation().locator('img[src="x"], script, b')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()

    // Stored exactly as typed, and shown as text in the notebook too.
    expect((await preference())?.never_share).toBe(hostile)
    await api.answer('rules')
    await api.answer('connect', { choice: 'done' })
    await page.goto('/dashboard')
    const notebook = await openNotebook(page)
    await expect(notebook.getByText(`asked that this is never shared with anyone: ${hostile}`)).toBeVisible()
    await expect(notebook.locator('img[src="x"], script, b')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()
    expect(dialogs).toBe(0)
  })

  test('HTML typed into the introduction and a person’s name is shown as text', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    const about = '<svg onload="window.__pwned=1"></svg><a href="javascript:alert(1)">me</a>'
    await meet.aboutBox().fill(about)
    await meet.send().click()
    await meet.expectStep('Your people')
    await expect(meet.line(about)).toBeVisible()
    await meet.people([{ name: '<i>Priya</i>', vip: true }])
    await expect(meet.line('Got it: <i>Priya</i>. <i>Priya</i> can always get through.')).toBeVisible()
    await expect(meet.conversation().locator('svg[onload], a[href^="javascript"], i')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined()
  })

  test('very long answers are accepted (cut to the stored limits), with no error', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    const about = 'I lead applied AI at PebbleRoad. ' + 'Client work, research and hiring. '.repeat(160) // ~5,300 characters
    await meet.aboutBox().fill(about)
    await meet.send().click()
    await meet.expectStep('Your people')
    await expect(meet.line(/^I lead applied AI at PebbleRoad\. Client work/)).toBeVisible()
    const said = await query<{ len: number }>("SELECT length(content) AS len FROM event WHERE direction = 'in' AND content LIKE 'I lead applied AI%'")
    expect(said).toEqual([{ len: 4000 }])

    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'], other: 'S'.repeat(500) })
    await api.answer('reach', { quiet_start: '22:00', quiet_end: '07:00' })
    await page.reload()
    await meet.expectStep('Sharing')
    await meet.neverShare().fill('Never my address. '.repeat(100)) // 1,800 characters
    await meet.share()
    const pref = await preference()
    expect(pref?.never_share.length).toBe(1000)
    expect(pref?.urgent_topics).toEqual(['A family emergency', 'S'.repeat(200)])
  })

  test('answers for a step you are not on are ignored; the introduction never jumps ahead', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const before = await totalEvents()
    for (const step of ['rules', 'connect', 'hello', 'done', 'bogus', '']) {
      const v = await api.answer(step, { choice: 'done' })
      expect(v.step, step).toBe('people')
    }
    expect(await totalEvents()).toBe(before)
    expect(await onboardingStep()).toBe('people')
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Your people')
  })

  test('answering again after the introduction is finished does not crash', async ({ api }) => {
    // I6, fixed: app/onboarding.py's answer() ignores a stale step or "done" before advancing.
    await api.completeIntroduction()
    const r = await api.post('/app/onboarding', { step: 'done', answer: {} })
    expect(r.status()).toBeLessThan(500)
  })

  test('when saving an answer fails, Ava says so, keeps the step, and you can try again', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    await page.route('**/api/app/onboarding', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Ava is busy. Try again in a minute.' }) })
        : route.continue(),
    )
    await meet.aboutBox().fill('I lead applied AI at PebbleRoad.')
    await meet.send().click()
    await expect(page.getByText('Ava is busy. Try again in a minute.')).toBeVisible()
    await meet.expectStep('About you')
    await expect(meet.line('I lead applied AI at PebbleRoad.')).toHaveCount(0) // the unsent line is taken back
    await expect(meet.aboutBox()).toBeVisible()

    await page.unroute('**/api/app/onboarding')
    await meet.aboutBox().fill('I lead applied AI at PebbleRoad.')
    await meet.send().click()
    await meet.expectStep('Your people')
    await expect(page.getByText('Ava is busy. Try again in a minute.')).toBeHidden()
    expect(await eventCount('I lead applied AI at PebbleRoad.', 'in')).toBe(1)
  })

  test('when saving fails, what you typed is still there to resend', async ({ page, api }) => {
    test.fail(true, 'BUG: the composer clears your text before sending; if the save fails, the text is lost')
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    await page.route('**/api/app/onboarding', (route) =>
      route.request().method() === 'POST' ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Ava is busy."}' }) : route.continue(),
    )
    await meet.aboutBox().fill('A long paragraph I would hate to type again.')
    await meet.send().click()
    await expect(page.getByText('Ava is busy.')).toBeVisible()
    await expect(meet.aboutBox()).toHaveValue('A long paragraph I would hate to type again.')
  })
})

// ----------------------------------------------------------------------------------
test.describe('coming back to the introduction', () => {
  test.use({ ownerState: 'hired', contextOptions: { reducedMotion: 'no-preference' } })
  test.beforeEach(({ owner }) => void owner) // signed up and hired before each test

  test('a reload mid-introduction resumes at the same step without repeating Ava', async ({ page, owner }) => {
    const name = first(owner.name)
    const meet = new Meet(page)
    await meet.open()
    await meet.hello()
    await meet.about('I lead applied AI at PebbleRoad.')
    await meet.settled()
    const before = await totalEvents()

    await page.reload()
    await meet.expectStep('Your people')
    // The history is shown at once, each line once, and the controls are ready.
    await expect(meet.peopleSubmit()).toBeVisible()
    await expect(meet.typing()).toBeHidden()
    for (const line of [AVA.hello(name), `Thanks, ${name}. I've noted that.`, 'I lead applied AI at PebbleRoad.']) {
      await expect(meet.line(line)).toHaveCount(1)
    }
    await expect(meet.line(new RegExp(`^${AVA.people}`))).toHaveCount(1)
    await expect(page.getByText('You hired Ava today')).toBeHidden()
    expect(await totalEvents()).toBe(before)

    // …and carries on from there.
    await meet.people([{ name: 'Priya', vip: true }])
    await expect(meet.line(new RegExp(`^${AVA.people}`))).toHaveCount(1)
  })

  test('reloading before saying anything does not repeat the greeting', async ({ page, owner }) => {
    const greeting = AVA.hello(first(owner.name))
    const meet = new Meet(page)
    await meet.open()
    await expect(meet.line(greeting)).toBeVisible()
    await page.reload()
    await page.reload()
    await expect(meet.chip('Nice to meet you')).toBeVisible()
    await expect(meet.line(greeting)).toHaveCount(1)
    expect(await eventCount(greeting, 'out')).toBe(1)
    expect(await totalEvents()).toBe(3)
  })

  test('"Leave for now" and coming back later continues where you left off', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { people: [{ name: 'Priya', relationship: 'Partner', vip: true }] })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Urgent')
    await meet.leave().click()
    await expect(page).toHaveURL(/\/$/)

    await page.getByRole('button', { name: 'Continue with Ava' }).click()
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Urgent')
    await expect(meet.line('Got it: Priya. Priya can always get through.')).toHaveCount(1)
    await meet.urgent()
    expect(await onboardingStep()).toBe('reach')
  })

  test('logging out mid-introduction and back in continues at the same step', async ({ page, api, owner }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Reaching you')
    await meet.leave().click()
    await logOut(page)
    await expect(page.getByRole('link', { name: 'Log in' })).toBeVisible()

    await page.goto('/meet')
    await expect(page).toHaveURL(/\/login\?next=%2Fmeet$/)
    await page.getByLabel('Email').fill(owner.email)
    await page.getByLabel('Password', { exact: true }).fill(owner.password)
    await page.getByRole('button', { name: 'Log in' }).click()
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Reaching you')
  })

  test('a second tab shows the same step; an answer from a stale tab never skips or repeats a step', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Your people')

    const other = await page.context().newPage()
    const meet2 = new Meet(other)
    await meet2.open()
    await meet2.expectStep('Your people')
    await meet2.peopleSkip().click()
    await meet2.expectStep('Urgent')

    // The first tab is now behind. Its answer doesn't move Ava on twice.
    await meet.personName(1).fill('Priya')
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    expect(await onboardingStep()).toBe('urgent')
    expect(await eventCount(AVA_URGENT_LINE, 'out')).toBe(1)
    await other.close()
  })

  test('the dashboard sends you back to the introduction until it is finished', async ({ page, api }) => {
    const meet = new Meet(page)
    await page.goto('/dashboard/chat')
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Hello')

    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await page.goto('/dashboard/chat')
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Your people')

    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    await api.answer('reach', { quiet_start: '22:00', quiet_end: '07:00' })
    await api.answer('share', { shareable: [] })
    await api.answer('rules')
    await page.goto('/dashboard/chat')
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Connect')

    await api.answer('connect', { choice: 'done' })
    await page.goto('/dashboard/chat')
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
    await expect(page.getByRole('heading', { name: /^Hello, \w+\. What shall we work on\?$/ })).toBeVisible()
    // The old /workspace link goes to the same chat.
    await page.goto('/workspace')
    await expect(page).toHaveURL(/\/dashboard\/chat$/)
    // After finishing, /meet shows the finished introduction with the way on.
    await page.goto('/meet')
    await meet.expectDone()
  })
})

// ----------------------------------------------------------------------------------
test.describe('double submission', () => {
  // With the usual animation on, the step's controls fade out while Ava "types", as a person sees them.
  test.use({ ownerState: 'hired', contextOptions: { reducedMotion: 'no-preference' } })
  test.beforeEach(({ owner }) => void owner) // signed up and hired before each test

  test('double-clicking a step never skips the next step', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const posts = trackPosts(page, '/api/app/onboarding')
    const meet = new Meet(page)
    await meet.open()
    await meet.personName(1).fill('Priya')
    await meet.peopleSubmit().dblclick()
    await meet.expectStep('Urgent')
    await posts.allFinished()
    expect(await onboardingStep()).toBe('urgent')
    await meet.urgentSubmit().dblclick()
    await meet.expectStep('Reaching you')
    await posts.allFinished()
    expect(await onboardingStep()).toBe('reach')
    await expect(meet.reachSubmit()).toBeVisible()
  })

  test('double-clicking Send in "Tell me here" sends once', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    const posts = trackPosts(page, '/api/app/onboarding')
    const meet = new Meet(page)
    await meet.open()
    await meet.aboutBox().fill('I lead applied AI at PebbleRoad.')
    await meet.send().dblclick()
    await meet.expectStep('Your people')
    expect(await posts.allFinished()).toBe(1)
    expect(await eventCount('I lead applied AI at PebbleRoad.', 'in')).toBe(1)
  })

  test('double-clicking a hello reply sends it once', async ({ page }) => {
    test.fail(true, 'BUG: a double-click on a step sends the answer twice (the fading-out button still takes clicks)')
    const posts = trackPosts(page, '/api/app/onboarding')
    const meet = new Meet(page)
    await meet.open()
    await meet.chip('Nice to meet you').dblclick()
    await meet.expectStep('About you')
    // Two requests race on the server; when they overlap, both are saved (see the API test below).
    expect(await posts.allFinished()).toBe(1)
    expect(await eventCount('Nice to meet you', 'in')).toBe(1)
  })

  test('double-clicking "These are my people" sends it once', async ({ page, api }) => {
    test.fail(true, 'BUG: a double-click on a step sends the answer twice (the fading-out button still takes clicks)')
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const posts = trackPosts(page, '/api/app/onboarding')
    const meet = new Meet(page)
    await meet.open()
    await meet.personName(1).fill('Priya')
    await meet.peopleSubmit().dblclick()
    await meet.expectStep('Urgent')
    expect(await posts.allFinished()).toBe(1)
    expect(await contacts()).toEqual([{ name: 'Priya', relationship: 'family', vip: true }])
  })

  test('the same answer sent several times at once is saved once (API)', async ({ page, api }) => {
    test.fail(true, 'BUG: /app/onboarding has no lock: overlapping answers each add the people and repeat Ava')
    const answer = { step: 'people', answer: { people: [{ name: 'Priya', relationship: 'Partner', vip: true }] } }
    // The requests race, so try a few fresh introductions; any duplicate shows the bug.
    for (let round = 0; round < 4; round++) {
      if (round > 0) {
        await resetDatabase()
        await page.context().clearCookies()
        await api.signup(newPerson())
        await api.hire()
      }
      await api.answer('hello', { choice: 'Nice to meet you' })
      await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
      const responses = await Promise.all(Array.from({ length: 5 }, () => api.post('/app/onboarding', answer)))
      for (const r of responses) expect(r.status()).toBe(200)
      expect(await onboardingStep()).toBe('urgent') // never skips past the next step
      if ((await contacts()).length > 1) break
    }
    expect(await contacts()).toEqual([{ name: 'Priya', relationship: 'partner', vip: true }])
    expect(await eventCount('Got it: Priya. Priya can always get through.', 'out')).toBe(1)
    expect(await eventCount(AVA_URGENT_LINE, 'out')).toBe(1)
  })
})
