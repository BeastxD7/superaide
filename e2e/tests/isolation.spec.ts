// Two owners, two Avas (ADR 0009). Anyone can hire Ava, and what each owner tells
// their Ava stays with that owner. Each owner runs in their own browser context
// (separate cookies), as two people on two computers would.
//
// Many owners is the default (ADR 0009, decided 2026-10-22): anyone can hire their own Ava.
// An account that hasn't hired sees none of an owner's data; two owners see only their own.
import type { Browser, BrowserContext } from '@playwright/test'
import { Api, expect, newPerson, test, type Person } from '../src/fixtures'
import { BASE_URL } from '../src/env'

const SECRET_A = 'my locker code is 4417'
const SECRET_B = 'my bike lock code is 9032'

type Owner = { person: Person; api: Api; context: BrowserContext }

async function account(browser: Browser, name: string): Promise<Owner> {
  const context = await browser.newContext({ baseURL: BASE_URL }) // its own cookie jar: another person
  const api = new Api(context.request)
  const person = newPerson(name)
  await api.signup(person)
  return { person, api, context }
}

async function hired(browser: Browser, name: string): Promise<Owner> {
  const owner = await account(browser, name)
  await owner.api.hire()
  return owner
}

async function tell(owner: Owner, text: string): Promise<void> {
  await owner.api.ok(owner.api.post('/app/chat', { text: `Remember that ${text}` }))
}

async function notebook(owner: Owner): Promise<string> {
  return JSON.stringify(await owner.api.ok(owner.api.get('/app/notebook')))
}

test.describe('two owners', () => {
  // `api` gives every test an empty database.
  test.beforeEach(async ({ api }) => {
    void api
  })

  test('an account that has not hired sees none of the owner’s data', async ({ browser }) => {
    const a = await hired(browser, 'Shashank Rao')
    await tell(a, SECRET_A)
    const b = await account(browser, 'Priya Tan')
    for (const path of ['/app/me', '/app/notebook', '/app/messages', '/app/people', '/app/preferences', '/app/reminders']) {
      const r = await b.api.get(path)
      expect(r.status(), path).toBe(403)
      expect(await r.text(), path).not.toContain('4417')
    }
    // Nor by guessing an id.
    const [fact] = (await a.api.ok<{ memories?: { id: string }[] }>(a.api.get('/app/notebook'))).memories ?? []
    if (fact) expect((await b.api.post(`/app/notebook/${fact.id}/forget`)).status()).toBe(403)
    expect(await notebook(a)).toContain('4417')
  })

  test('the marketplace never names who hired Ava', async ({ browser }) => {
    const a = await hired(browser, 'Shashank Rao')
    const b = await account(browser, 'Priya Tan')
    const listing = await b.api.ok<unknown>(b.api.get('/app/agents'))
    expect(JSON.stringify(listing)).not.toContain(a.person.name)
    expect(JSON.stringify(listing)).not.toContain(a.person.email)
  })

  test('a second owner can hire their own Ava', async ({ browser }) => {
    await hired(browser, 'Shashank Rao')
    const b = await account(browser, 'Priya Tan')
    const r = await b.api.post('/app/hire', { agent: 'ava', accepted_notice: true })
    expect(r.status()).toBe(200)
  })

  test('the marketplace shows Ava as available to everyone', async ({ browser }) => {
    await hired(browser, 'Shashank Rao')
    const b = await account(browser, 'Priya Tan')
    const listing = await b.api.ok<{ slug: string; hired?: boolean }[]>(b.api.get('/app/agents'))
    expect(listing.find((x) => x.slug === 'ava')?.hired).toBeFalsy()
  })

  test('each owner’s notebook holds only what they told their own Ava', async ({ browser }) => {
    const a = await hired(browser, 'Shashank Rao')
    const b = await hired(browser, 'Priya Tan')
    await tell(a, SECRET_A)
    await tell(b, SECRET_B)
    const [na, nb] = [await notebook(a), await notebook(b)]
    expect(na).toContain('4417')
    expect(na).not.toContain('9032')
    expect(nb).toContain('9032')
    expect(nb).not.toContain('4417')
  })

  test('each owner’s conversation, reminders and people stay their own', async ({ browser }) => {
    const a = await hired(browser, 'Shashank Rao')
    const b = await hired(browser, 'Priya Tan')
    await tell(a, SECRET_A)
    await a.api.ok(a.api.post('/app/reminders', { text: 'Call the bank tomorrow at 5pm' }))
    for (const path of ['/app/messages', '/app/reminders', '/app/people']) {
      const seen = JSON.stringify(await b.api.ok(b.api.get(path)))
      expect(seen, path).not.toContain('4417')
      expect(seen, path).not.toContain('Call the bank')
    }
  })

  test('one owner cannot act on the other’s memories by id', async ({ browser }) => {
    const a = await hired(browser, 'Shashank Rao')
    const b = await hired(browser, 'Priya Tan')
    await tell(a, SECRET_A)
    const [fact] = (await a.api.ok<{ memories?: { id: string }[] }>(a.api.get('/app/notebook'))).memories ?? []
    expect(fact).toBeTruthy()
    expect((await b.api.post(`/app/notebook/${fact.id}/forget`)).status()).toBe(404)
    expect(await notebook(a)).toContain('4417')
  })
})
