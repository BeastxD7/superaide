import type { Page } from '@playwright/test'

// The e2e stack runs with triage off, so /app/me says `features.triage: false` and the app never asks for the
// Needs-you list. A spec that mocks /needs-you calls this first, so the app behaves as if triage were on.
export async function triageOn(page: Page): Promise<void> {
  await page.route('**/api/app/me', async (route) => {
    // The app re-reads /me on focus and on a poll (a shared state), so a read can still be in flight when a test ends or its page
    // closes. That is not a failure of the test: let it go quietly.
    try {
      const res = await route.fetch()
      const body = await res.json()
      const target = body && typeof body === 'object' && 'data' in body && body.data ? body.data : body
      target.features = { ...(target.features ?? {}), triage: true }
      await route.fulfill({ response: res, json: body })
    } catch {
      await route.abort().catch(() => undefined)
    }
  })
}
