// Hiring and the introduction on a phone (Pixel 7): the answer controls and the composer
// stay on screen and usable, and Ava's latest line is never hidden behind them.
import { expect, test } from '../src/fixtures'
import { AVA, HirePage, Meet, contacts, onboardingStep, openNotebook } from '../src/helpers/introduction'
import type { Locator, Page } from '@playwright/test'

test.use({ contextOptions: { reducedMotion: 'reduce' }, timezoneId: 'Asia/Tokyo' })

async function box(l: Locator) {
  const b = await l.boundingBox()
  expect(b, 'element has a box').not.toBeNull()
  return b!
}

// No sideways scrolling of the page.
async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const { scroll, width } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }))
  expect(scroll, 'page is wider than the screen').toBeLessThanOrEqual(width)
}

// `line` is fully visible in the conversation, and the conversation ends above `control`.
async function expectAboveControls(meet: Meet, line: Locator, control: Locator): Promise<void> {
  await control.scrollIntoViewIfNeeded()
  await expect(control).toBeInViewport({ ratio: 1 })
  await expect(line).toBeInViewport({ ratio: 1 })
  const main = await box(meet.conversation())
  const l = await box(line)
  const c = await box(control)
  expect(l.y, 'line starts inside the conversation').toBeGreaterThanOrEqual(main.y - 1)
  expect(l.y + l.height, 'line ends inside the conversation').toBeLessThanOrEqual(main.y + main.height + 1)
  expect(main.y + main.height, 'conversation ends above the controls').toBeLessThanOrEqual(c.y + 1)
}

test.describe('hiring on a phone', () => {
  test.use({ ownerState: 'account' })

  test('the notice, its box and the Hire button fit the screen and work', async ({ page, owner }) => {
    void owner
    const hire = new HirePage(page)
    await hire.open()
    await expectNoHorizontalScroll(page)
    await hire.agree().scrollIntoViewIfNeeded()
    await expect(hire.agree()).toBeInViewport()
    await hire.agree().check()
    await hire.hireButton().scrollIntoViewIfNeeded()
    await expect(hire.hireButton()).toBeInViewport({ ratio: 1 })
    await hire.hireButton().click()
    await expect(hire.hired()).toBeVisible()
    await expect(hire.sayHello()).toBeInViewport({ ratio: 1 })
    await expectNoHorizontalScroll(page)
    await hire.sayHello().click()
    await expect(page).toHaveURL(/\/meet$/)
  })
})

test.describe('the introduction on a phone', () => {
  test.use({ ownerState: 'hired' })
  test.beforeEach(({ owner }) => void owner) // signed up and hired before each test

  test('every step: the controls are on screen and Ava’s latest line sits above them', async ({ page, owner }) => {
    const meet = new Meet(page)
    await meet.open()

    // Hello
    await meet.expectStep('Hello')
    await expectAboveControls(meet, meet.line(/^I'm new, so I start at the Infant stage/), meet.chip('Nice to meet you'))
    await expect(meet.leave()).toBeInViewport()
    await expectNoHorizontalScroll(page)
    await meet.hello()

    // About: the composer and Send are on screen
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.about}`)), meet.aboutBox())
    await expect(meet.send()).toBeInViewport({ ratio: 1 })
    await meet.aboutBox().fill('I lead applied AI at PebbleRoad in Singapore.')
    await meet.send().click()
    await meet.expectStep('Your people')

    // People
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.people}`)), meet.personName(1))
    await meet.people([{ name: 'Priya', relationship: 'Partner', vip: true }])
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.urgent}`)), meet.urgentSubmit())

    // Urgent → Reaching you → Sharing → Ground rules → Connect
    await meet.urgent()
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.reach}`)), meet.reachSubmit())
    await expect(meet.quietStart()).toBeVisible()
    await meet.reach()
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.share}`)), meet.shareSubmit())
    await meet.share()
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.rules}`)), meet.rulesSubmit())
    await meet.rules()
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.connect}`)), meet.finish())
    await meet.connect()
    await expectAboveControls(meet, meet.line(/^So far I know/), meet.startWorking())
    await expectNoHorizontalScroll(page)

    // The workspace's notebook tab shows what was said.
    await meet.startWorking().click()
    const notebook = await openNotebook(page)
    await expect(notebook.getByText(`Priya is ${owner.name}'s partner; they can always get through, even when busy.`)).toBeVisible()
    expect(await contacts()).toEqual([{ name: 'Priya', relationship: 'partner', vip: true }])
  })

  test('ten people: the list scrolls inside the panel, the conversation keeps room, and the button is reachable', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    for (let i = 3; i <= 10; i++) await meet.addSomeone().click()
    for (let i = 1; i <= 10; i++) await meet.personName(i).fill(`Person ${i}`)
    // The panel is capped, so the conversation is still there to read.
    const main = await box(meet.conversation())
    expect(main.height).toBeGreaterThan(120)
    await expect(meet.line(new RegExp(`^${AVA.people}`))).toBeInViewport()
    await expectAboveControls(meet, meet.line(new RegExp(`^${AVA.people}`)), meet.peopleSubmit())
    await meet.peopleSubmit().click()
    await meet.expectStep('Urgent')
    expect(await contacts()).toHaveLength(10)
  })

  test('the hint for a wrong answer shows on screen above the controls', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'] })
    const meet = new Meet(page)
    await meet.open()
    await meet.quietStart().fill('')
    await meet.reachSubmit().click()
    const hint = page.getByText('Use times like 22:00 and 07:00.')
    await hint.scrollIntoViewIfNeeded()
    await expect(hint).toBeInViewport({ ratio: 1 })
    await expect(meet.reachSubmit()).toBeVisible()
    expect(await onboardingStep()).toBe('reach')
  })

  test('a long link in your introduction wraps inside the screen', async ({ page, api }) => {
    test.fail(true, 'BUG: chat bubbles use pre-wrap without word breaking, so a long link runs off the side of a phone screen')
    await api.answer('hello', { choice: 'Nice to meet you' })
    const meet = new Meet(page)
    await meet.open()
    const link = 'https://docs.google.com/document/d/1xQ7vB3kLmN9pR2sT5uW8yZ0aC4eG6iK1mO3qS5uW7yA/edit'
    await meet.aboutBox().fill(`My bio is here: ${link}`)
    await meet.send().click()
    await meet.expectStep('Your people')
    const bubble = meet.line(`My bio is here: ${link}`)
    await expect(bubble).toBeVisible()
    // Where the text itself ends, not just its box.
    const textRight = await bubble.evaluate((el) => {
      const range = document.createRange()
      range.selectNodeContents(el)
      return range.getBoundingClientRect().right
    })
    expect(textRight, 'the link runs past the right edge of the screen').toBeLessThanOrEqual(page.viewportSize()!.width)
    expect(await bubble.evaluate((el) => el.scrollWidth - el.clientWidth), 'text overflows its bubble').toBeLessThanOrEqual(0)
  })

  test('"Leave for now" is on screen; coming back from the marketplace continues', async ({ page, api }) => {
    await api.answer('hello', { choice: 'Nice to meet you' })
    await api.answer('about', { mode: 'chat', text: 'I lead applied AI.' })
    const meet = new Meet(page)
    await meet.open()
    await meet.expectStep('Your people')
    await expect(meet.leave()).toBeInViewport({ ratio: 1 })
    await meet.leave().click()
    await expect(page).toHaveURL(/\/$/)
    const back = page.getByRole('button', { name: 'Continue with Ava' })
    await expect(back).toBeInViewport()
    await back.click()
    await expect(page).toHaveURL(/\/meet$/)
    await meet.expectStep('Your people')
    await expect(meet.peopleSubmit()).toBeVisible()
  })
})
