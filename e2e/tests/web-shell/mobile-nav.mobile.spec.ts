// The installable app and the phone's tab bar (ADR 0011 M0 and M6), on a Pixel 7.
//
// Both are build-time flags. A normal build has them off, and the "flags off" tests check that
// nothing changed. To test them on, build the frontend with NEXT_PUBLIC_PWA=1 and
// NEXT_PUBLIC_MOBILE_TABS=1 and run this file with E2E_MOBILE_FLAGS=1.
import { expect, test } from '../../src/fixtures'
import { composer, expectNoSidewaysScroll, logOutFromSidebar, openChat, sendButton } from '../../src/helpers/workspace'

const FLAGS_ON = process.env.E2E_MOBILE_FLAGS === '1'

const tabBar = (page: import('@playwright/test').Page) => page.getByRole('navigation', { name: 'Main' })

test.describe('flags off: the dashboard is unchanged', () => {
  test.skip(FLAGS_ON, 'this build has the mobile flags on')

  test('no tab bar, no manifest, no service worker', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(tabBar(page)).toHaveCount(0)
    await expect(page.locator('link[rel="manifest"]')).toHaveCount(0)
    expect((await page.request.get('/app.webmanifest')).status()).toBe(404)
    const registrations = await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)
    expect(registrations).toBe(0)
  })
})

test.describe('flags on: the phone tab bar', () => {
  test.skip(!FLAGS_ON, 'build with NEXT_PUBLIC_MOBILE_TABS=1 and set E2E_MOBILE_FLAGS=1')

  test('the tab bar moves between Chat, Approvals and Reminders, with More for the rest', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const bar = tabBar(page)
    await expect(bar).toBeVisible()
    await expect(bar.getByRole('link', { name: 'Chat' })).toHaveAttribute('aria-current', 'page')
    // Needs you comes with triage; until then it's there but disabled.
    await expect(bar.getByText('Needs you')).toBeVisible()
    await expect(bar.getByRole('link', { name: /Needs you/ })).toHaveCount(0)

    await bar.getByRole('link', { name: 'Approvals' }).tap()
    await expect(page).toHaveURL(/\/dashboard\/approvals$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible()
    await expect(page.getByText('Nothing is waiting for your OK.')).toBeVisible()
    await expect(bar.getByRole('link', { name: 'Approvals' })).toHaveAttribute('aria-current', 'page')
    await expectNoSidewaysScroll(page)

    await bar.getByRole('link', { name: 'Reminders' }).tap()
    await expect(page).toHaveURL(/\/dashboard\/reminders$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Reminders' })).toBeVisible()
    await expectNoSidewaysScroll(page)

    await bar.getByRole('button', { name: 'More' }).tap()
    await expect(page.getByRole('link', { name: 'Notebook', exact: true })).toBeVisible()
    await page.getByRole('link', { name: 'Notebook', exact: true }).tap()
    await expect(page).toHaveURL(/\/dashboard\/notebook$/)
  })

  test('the composer stays above the tab bar, and chat still works', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(composer(page)).toBeInViewport()
    await expect(sendButton(page)).toBeInViewport()
    const [input, bar] = await Promise.all([composer(page).boundingBox(), tabBar(page).boundingBox()])
    expect(input && bar && input.y + input.height <= bar.y + 1, 'the composer sits above the tab bar').toBeTruthy()
    await composer(page).fill('Remember that I park on level B2')
    await sendButton(page).tap()
    await expect(page.getByText('Saved to your notebook. Only you can see it.')).toBeVisible()
    await expectNoSidewaysScroll(page)
  })

  test('on a wide screen there is no tab bar', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    await openChat(page)
    await expect(tabBar(page)).toBeHidden()
  })
})

test.describe('flags on: the installable app', () => {
  test.skip(!FLAGS_ON, 'build with NEXT_PUBLIC_PWA=1 and set E2E_MOBILE_FLAGS=1')

  test('the manifest is served and valid', async ({ page, owner }) => {
    void owner
    await openChat(page)
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/app.webmanifest')
    const res = await page.request.get('/app.webmanifest')
    expect(res.status()).toBe(200)
    expect(res.headers()['content-type']).toContain('application/manifest+json')
    const m = await res.json()
    expect(m).toMatchObject({ id: '/dashboard', name: 'Ava', display: 'standalone', scope: '/' })
    expect(m.start_url).toMatch(/^\/dashboard/)
    const sizes = m.icons.map((i: { sizes: string; purpose: string }) => `${i.sizes}:${i.purpose}`)
    expect(sizes).toEqual(expect.arrayContaining(['192x192:any', '512x512:any', '192x192:maskable', '512x512:maskable']))
    for (const icon of m.icons) {
      const r = await page.request.get(icon.src)
      expect(r.status(), icon.src).toBe(200)
      expect(r.headers()['content-type']).toBe('image/png')
    }
  })

  test('the service worker registers, is never cached, and caches nothing private', async ({ page, owner }) => {
    void owner
    await openChat(page)
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope)
    expect(new URL(scope).pathname).toBe('/')
    const sw = await page.request.get('/sw.js')
    expect(sw.headers()['cache-control']).toContain('no-store')
    expect(sw.headers()['service-worker-allowed']).toBe('/')
    expect(await sw.text()).not.toMatch(/addEventListener\(\s*['"]fetch['"]/)

    // Use the app (it calls /api all the time), then look in every cache.
    await page.goto('/dashboard/reminders')
    await page.goto('/dashboard/notebook')
    await openChat(page)
    const cached = await page.evaluate(async () => {
      const urls: string[] = []
      for (const name of await caches.keys()) {
        for (const req of await (await caches.open(name)).keys()) urls.push(req.url)
      }
      return urls
    })
    expect(cached.filter((u) => new URL(u).pathname.startsWith('/api'))).toEqual([])
    expect(cached).toEqual([]) // the service worker has no cache at all
  })

  test('logging out forgets this device for push and ends its subscription', async ({ page, owner }) => {
    // A stand-in subscription: headless Chromium can't reach a real push service.
    await page.addInitScript(() => {
      const fake = {
        endpoint: 'https://fcm.googleapis.com/fcm/send/e2e-device',
        unsubscribe: async () => {
          sessionStorage.setItem('unsubscribed', '1')
          return true
        },
        toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/fcm/send/e2e-device', keys: {} }),
      }
      PushManager.prototype.getSubscription = async () => fake as unknown as PushSubscription
    })
    const deletes: string[] = []
    await page.route('**/api/app/push/subscription', async (route) => {
      if (route.request().method() === 'DELETE') deletes.push(route.request().postData() ?? '')
      await route.fulfill({ status: 404, body: '{"detail":"Not Found"}' }) // push is off on this server
    })
    await openChat(page)
    await page.evaluate(async () => navigator.serviceWorker.ready)
    await logOutFromSidebar(page, owner)
    expect(deletes).toEqual([JSON.stringify({ endpoint: 'https://fcm.googleapis.com/fcm/send/e2e-device' })])
    expect(await page.evaluate(() => sessionStorage.getItem('unsubscribed'))).toBe('1')
  })
})
