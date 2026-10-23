import type { Page } from '@playwright/test'

// The e2e stack runs with triage off, so /app/me says `features.triage: false` and the app never asks for the
// Needs-you list. A spec that mocks /needs-you calls this first, so the app behaves as if triage were on.
export async function triageOn(page: Page): Promise<void> {
  await page.route('**/api/app/me', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    const target = body && typeof body === 'object' && 'data' in body && body.data ? body.data : body
    target.features = { ...(target.features ?? {}), triage: true }
    await route.fulfill({ response: res, json: body })
  })
}
