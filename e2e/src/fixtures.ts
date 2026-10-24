// Shared test setup. Import `test` and `expect` from here, not from @playwright/test.
//
//   test('…', async ({ page, api }) => …)            fresh database, signed-out browser
//   test('…', async ({ page, owner }) => …)          signed in, Ava hired, introduction done
//   test.use({ ownerState: 'hired' })                 …or stop after hiring (introduction not started)
//
// `api` calls the app's API from the same browser context, so its cookies are the
// page's cookies: signing up through `api` signs the page in too.
import { test as base, expect, type APIRequestContext, type APIResponse } from '@playwright/test'
import { query, resetDatabase } from './db'
import { PASSWORD } from './env'

export type Person = { name: string; email: string; password: string }
export type OwnerState = 'account' | 'hired' | 'introduced'

let counter = 0
export function newPerson(name = 'Ananya Menon'): Person {
  counter += 1
  const stamp = `${Date.now().toString(36)}${counter}`
  return { name, email: `e2e-${stamp}@example.com`, password: PASSWORD }
}

export class Api {
  constructor(private readonly request: APIRequestContext) {}

  private headers = { 'X-Requested-With': 'superaide-e2e' }

  get(path: string): Promise<APIResponse> {
    return this.request.get(`/api${path}`)
  }

  post(path: string, data?: unknown): Promise<APIResponse> {
    return this.request.post(`/api${path}`, { data: data ?? {}, headers: this.headers })
  }

  // The same POST without the CSRF header, to check the API refuses it.
  postWithoutCsrfHeader(path: string, data?: unknown): Promise<APIResponse> {
    return this.request.post(`/api${path}`, { data: data ?? {} })
  }

  async ok<T>(res: Promise<APIResponse>): Promise<T> {
    const r = await res
    expect(r.ok(), `${r.url()} → ${r.status()} ${await r.text()}`).toBeTruthy()
    return (await r.json()) as T
  }

  signup(p: Person) {
    // Signs up as a browser set to Singapore time would (the app sends the browser's time zone).
    return this.ok(this.post('/auth/signup', { name: p.name, email: p.email, password: p.password, timezone: 'Asia/Singapore' }))
  }

  login(email: string, password: string) {
    return this.post('/auth/login', { email, password })
  }

  hire(agent = 'ava') {
    return this.ok(this.post('/app/hire', { agent, accepted_notice: true }))
  }

  answer(step: string, answer: Record<string, unknown> = {}) {
    return this.ok<{ step: string; done: boolean; hint?: string }>(this.post('/app/onboarding', { step, answer }))
  }

  // Walk the whole introduction through the API (fast), with typical answers.
  async completeIntroduction(): Promise<void> {
    await this.ok(this.get('/app/onboarding'))
    await this.answer('hello', { choice: 'What will you do for me?' })
    await this.answer('about', { mode: 'chat', text: 'I lead applied AI at Larkfield in Singapore.' })
    await this.answer('people', {
      people: [
        { name: 'Priya', relationship: 'Partner', vip: true },
        { name: 'Arjun', relationship: 'Team', vip: false },
      ],
    })
    await this.answer('urgent', { topics: ['A family emergency'], other: 'Anything about the Series A', digest: 'morning' })
    await this.answer('reach', { channel: 'app', quiet_start: '22:00', quiet_end: '07:00', vip_breaks_quiet: true, timezone: 'Asia/Singapore' })
    await this.answer('share', { shareable: ['availability'], never: 'My home address' })
    await this.answer('rules')
    const done = await this.answer('connect', { choice: 'done' })
    expect(done.done).toBe(true)
  }
}

type Fixtures = {
  api: Api
  ownerState: OwnerState
  owner: Person
}

type WorkerFixtures = { silentBrowser: void }

// Tests never make a sound. Ava's voice (speechSynthesis) is played by the operating system,
// outside the browser's own audio, so --mute-audio in playwright.config.ts doesn't cover it:
// every utterance speaks at volume 0 instead, with its timing and events unchanged.
function silence(): void {
  const synth = window.speechSynthesis
  if (!synth) return
  const speak = synth.speak.bind(synth)
  synth.speak = (u: SpeechSynthesisUtterance) => {
    u.volume = 0
    speak(u)
  }
}

export const test = base.extend<Fixtures, WorkerFixtures>({
  // Every context a test opens is silent: the default one, and any from browser.newContext().
  silentBrowser: [
    async ({ browser }, use) => {
      const newContext = browser.newContext.bind(browser)
      browser.newContext = async (options) => {
        const context = await newContext(options)
        await context.addInitScript(silence)
        return context
      }
      await use()
    },
    { scope: 'worker', auto: true },
  ],
  context: async ({ context }, use) => {
    await context.addInitScript(silence)
    await use(context)
  },
  // Every test starts from an empty database.
  api: async ({ page }, use) => {
    await resetDatabase()
    await use(new Api(page.request))
  },
  ownerState: ['introduced', { option: true }],
  owner: async ({ api, ownerState }, use) => {
    const person = newPerson()
    await api.signup(person)
    if (ownerState !== 'account') await api.hire()
    if (ownerState === 'introduced') await api.completeIntroduction()
    await use(person)
  },
})

export { expect, query }
