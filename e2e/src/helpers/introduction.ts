// Helpers for the hiring and introduction specs (tests/introduction*.spec.ts).
// They drive the screens the way a person would: by role, label and visible text.
import { expect, type Locator, type Page } from '@playwright/test'
import { query } from '../db'

export const STEP_LABELS = ['Hello', 'About you', 'Your people', 'Urgent', 'Reaching you', 'Sharing', 'Ground rules', 'Connect'] as const
export type StepLabel = (typeof STEP_LABELS)[number]

// Ava's opening lines for each step (from backend/src/superaide/app/onboarding.py).
export const AVA = {
  hello: (first: string) => `Hi ${first}, I'm Ava, your personal assistant. Thanks for hiring me.`,
  about: "Let's start with you. Pick whatever's easiest",
  people: 'Who matters most to you?',
  urgent: 'What counts as urgent for you?',
  reach: 'When something is urgent, how should I reach you?',
  share: 'People will ask me things about you.',
  rules: "Before we start, here's how I work",
  connect: 'Last step, and it\'s optional',
  done: (first: string) => `That's us set up, ${first}.`,
}

export const ABOUT_PLACEHOLDER = /I run a 40-person AI consultancy/

// The hire screen: the notice, its checkbox and the button.
export class HirePage {
  constructor(readonly page: Page) {}
  readonly agree = () => this.page.getByRole('checkbox', { name: /I understand Ava is an AI/ })
  readonly hireButton = () => this.page.getByRole('button', { name: 'Hire Ava' })
  readonly hired = () => this.page.getByRole('heading', { name: 'Ava is on your team' })
  readonly sayHello = () => this.page.getByRole('button', { name: 'Say hello to Ava' })

  async open(): Promise<void> {
    await this.page.goto('/agents/ava/hire')
    await expect(this.page.getByRole('heading', { name: /One thing before you start/ })).toBeVisible()
    // The notice text arrives from /agents; wait for it so the button can enable.
    await expect(this.page.getByText('Ava is an AI personal assistant.')).toBeVisible()
  }
}

// The introduction screen (/meet).
export class Meet {
  constructor(readonly page: Page) {}

  readonly conversation = () => this.page.getByRole('main')
  readonly leave = () => this.page.getByRole('link', { name: 'Leave for now' })
  readonly typing = () => this.page.getByText('Ava is typing…')

  // "Getting to know you. Step 3 of 8: Your people"
  async expectStep(label: StepLabel): Promise<void> {
    const n = STEP_LABELS.indexOf(label) + 1
    await expect(this.page.getByText(`Step ${n} of ${STEP_LABELS.length}: ${label}`, { exact: false })).toBeVisible()
  }

  async expectDone(): Promise<void> {
    await expect(this.page.getByText('All set', { exact: true })).toBeVisible()
    await expect(this.startWorking()).toBeVisible()
  }

  // Lines in the conversation that match `text` exactly (Ava's or yours).
  line(text: string | RegExp): Locator {
    return this.conversation().getByText(text, { exact: typeof text === 'string' })
  }

  // --- step controls --------------------------------------------------------
  readonly chip = (name: string) => this.page.getByRole('button', { name, exact: true })
  readonly tab = (name: 'Tell me here' | 'Files & links' | 'Video call') => this.page.getByRole('tab', { name })
  readonly aboutBox = () => this.page.getByRole('textbox', { name: ABOUT_PLACEHOLDER })
  readonly send = () => this.page.getByRole('button', { name: 'Send' })
  readonly personName = (n: number) => this.page.getByLabel(`Person ${n} name`)
  readonly personRelationship = (n: number) => this.page.getByLabel(`Person ${n} relationship`)
  readonly removePerson = (n: number) => this.page.getByRole('button', { name: `Remove person ${n}` })
  readonly addSomeone = () => this.page.getByRole('button', { name: 'Add someone' })
  readonly peopleSubmit = () => this.page.getByRole('button', { name: 'These are my people' })
  readonly peopleSkip = () => this.page.getByRole('button', { name: 'Skip for now' })
  readonly urgentChip = (name: string) => this.page.getByRole('button', { name, exact: true })
  readonly urgentOther = () => this.page.getByPlaceholder(/Something else\?/)
  readonly digest = (name: string) => this.page.getByRole('button', { name, exact: true })
  readonly urgentSubmit = () => this.page.getByRole('button', { name: "That's what's urgent" })
  readonly quietStart = () => this.page.getByLabel('Quiet hours start')
  readonly quietEnd = () => this.page.getByLabel('Quiet hours end')
  readonly reachSubmit = () => this.page.getByRole('button', { name: 'Save how to reach me' })
  readonly shareToggle = (label: string) => this.page.getByRole('switch', { name: label })
  readonly neverShare = () => this.page.getByPlaceholder(/Anything I must never share\?/)
  readonly shareSubmit = () => this.page.getByRole('button', { name: "That's what you can share" })
  readonly shareSkip = () => this.page.getByRole('button', { name: 'Share nothing for now' })
  readonly rulesSubmit = () => this.page.getByRole('button', { name: 'Sounds good' })
  readonly finish = () => this.page.getByRole('button', { name: 'Finish' })
  readonly startWorking = () => this.page.getByRole('button', { name: 'Start working with Ava' })

  async open(): Promise<void> {
    await this.page.goto('/meet')
    await expect(this.page).toHaveURL(/\/meet$/)
  }

  // --- whole steps, with typical answers --------------------------------------
  async hello(choice: 'Nice to meet you' | 'What will you do for me?' = 'Nice to meet you'): Promise<void> {
    await this.chip(choice).click()
    await this.expectStep('About you')
  }

  async about(text: string): Promise<void> {
    await this.tab('Tell me here').click()
    await this.aboutBox().fill(text)
    await this.send().click()
    await this.expectStep('Your people')
  }

  async people(rows: { name: string; relationship?: string; vip?: boolean }[]): Promise<void> {
    for (let i = 0; i < rows.length; i++) {
      const n = i + 1
      if (!(await this.personName(n).isVisible())) await this.addSomeone().click()
      await this.personName(n).fill(rows[i].name)
      if (rows[i].relationship) await this.personRelationship(n).selectOption(rows[i].relationship!)
      const vip = this.page.getByRole('switch', { name: `${rows[i].name} can always get through` })
      if ((await vip.getAttribute('aria-checked')) !== String(Boolean(rows[i].vip))) await vip.click()
    }
    await this.peopleSubmit().click()
    await this.expectStep('Urgent')
  }

  async urgent(): Promise<void> {
    await this.urgentSubmit().click()
    await this.expectStep('Reaching you')
  }

  async reach(): Promise<void> {
    await this.reachSubmit().click()
    await this.expectStep('Sharing')
  }

  async share(): Promise<void> {
    await this.shareSubmit().click()
    await this.expectStep('Ground rules')
  }

  async rules(): Promise<void> {
    await this.rulesSubmit().click()
    await this.expectStep('Connect')
  }

  async connect(): Promise<void> {
    await this.finish().click()
    await this.expectDone()
  }

  // The controls only show once Ava has finished "typing" her lines.
  async settled(): Promise<void> {
    await expect(this.typing()).toBeHidden()
  }
}

// --- after the introduction ---------------------------------------------------------

// Ava's notebook, its own page in the dashboard (/dashboard/notebook). Call it once
// the dashboard has opened (e.g. after "Start working with Ava"). Returns the page's main region.
export async function openNotebook(page: Page): Promise<Locator> {
  await expect(page).toHaveURL(/\/dashboard/)
  await page.goto('/dashboard/notebook')
  const region = page.getByRole('main')
  await expect(region.getByRole('heading', { name: "Ava's notebook" })).toBeVisible()
  return region
}

// Log out from a page with the site's navigation: a "Log out" button, or the account menu.
export async function logOut(page: Page): Promise<void> {
  const direct = page.getByRole('button', { name: 'Log out' })
  const menu = page.getByRole('button', { name: /^Account: signed in as/ })
  await expect(direct.or(menu).first()).toBeVisible()
  if (!(await direct.isVisible())) await menu.click()
  await direct.click()
}

// Count the API calls a page makes to one endpoint, so a test can wait until every
// one of them (a double-click can send two) has finished before checking the database.
export function trackPosts(page: Page, path: string) {
  let sent = 0
  let finished = 0
  const matches = (url: string, method: string) => method === 'POST' && new URL(url).pathname === path
  page.on('request', (r) => {
    if (matches(r.url(), r.method())) sent += 1
  })
  const done = (r: { url(): string; method(): string }) => {
    if (matches(r.url(), r.method())) finished += 1
  }
  page.on('requestfinished', done)
  page.on('requestfailed', done)
  return {
    sent: () => sent,
    async allFinished(): Promise<number> {
      await expect.poll(() => sent - finished, { message: `waiting for POST ${path} to finish` }).toBe(0)
      return sent
    },
  }
}

// --- the database -----------------------------------------------------------------

export async function onboardingStep(): Promise<string | undefined> {
  const rows = await query<{ step: string }>('SELECT step FROM onboarding')
  return rows[0]?.step
}

export async function contacts(): Promise<{ name: string; relationship: string; vip: boolean }[]> {
  return query(
    `SELECT p.display_name AS name, c.relationship, c.vip
       FROM contact c JOIN person p ON p.id = c.person_id
      WHERE c.removed_at IS NULL
      ORDER BY p.display_name`,
  )
}

export type Preference = {
  urgent_topics: string[]
  digest: string
  interrupt_channel: string
  quiet_start: string
  quiet_end: string
  vip_breaks_quiet: boolean
  timezone: string
  shareable: string[]
  never_share: string
}

export async function preference(): Promise<Preference | undefined> {
  const rows = await query<Preference>(
    `SELECT urgent_topics, digest, interrupt_channel, quiet_start, quiet_end, vip_breaks_quiet, timezone, shareable, never_share
       FROM owner_preference`,
  )
  return rows[0]
}

// How many times a line appears in the stored conversation.
export async function eventCount(content: string, direction?: 'in' | 'out'): Promise<number> {
  const rows = await query<{ n: string }>(
    `SELECT count(*) AS n FROM event WHERE content = $1 ${direction ? 'AND direction = $2' : ''}`,
    direction ? [content, direction] : [content],
  )
  return Number(rows[0].n)
}

export async function totalEvents(): Promise<number> {
  const rows = await query<{ n: string }>('SELECT count(*) AS n FROM event')
  return Number(rows[0].n)
}

export async function memories(): Promise<{ content: string; category: string; source: string }[]> {
  return query('SELECT content, category, source FROM memory_item WHERE valid_to IS NULL ORDER BY created_at')
}
