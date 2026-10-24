// Issue #117: the owner's Promote control for the kNN head. Ava only shows "eligible" with the numbers; the owner presses
// the button; taking it back is always there. The triage API is scripted with page.route (the promotion rules are covered
// by the backend tests), and /app/me is patched to say triage is on so the Needs-you page opens.
import type { Page } from '@playwright/test'
import { expect, test } from '../../src/fixtures'

const base = {
  ladder: [{ provider: 'app', delay_s: 0 }], expire_after_s: 3600, max_per_hour: 4, max_per_day: 12, vip_ignores_budget: 'critical',
  theta_interrupt: 0.6, theta_handle: 0.7, silenced_people: [], silenced_topics: [], autonomy_grants: [], away_until: null,
  away_visibility: 'colleagues', busy_until: null, tell_sender_seen: false, phone_enabled: false,
}
const shadow = {
  enabled: true, mode: 'shadow', n: 240, agree: 220, agreement: 0.92, lower_bound: 0.88, needs: 200, gate: 0.8, z: 1.96, window_days: 30,
  eligible: true, text: 'Shadow: agrees with you 92% over 240 corrections; it still decides nothing until you say so.',
  promoted: false, can_promote: true, can_demote: false, raise_enabled: true, acting: false, demote_after_days: 14,
}

async function open(page: Page, card: typeof shadow & Record<string, unknown>) {
  let current = card
  await page.route('**/api/app/me', async (r) => {
    const res = await r.fetch()
    const me = await res.json()
    await r.fulfill({ response: res, json: { ...me, features: { ...me.features, triage: true } } })
  })
  await page.route('**/api/app/needs-you', (r) => r.fulfill({ json: { items: [], counts: { needs_you: 0, held: 0, handled: 0 } } }))
  await page.route('**/api/app/triage/settings', (r) => r.fulfill({ json: { ...base, knn_shadow: current } }))
  await page.route('**/api/app/triage/knn/promote', (r) => {
    current = { ...current, promoted: true, can_promote: false, can_demote: true, acting: true }
    return r.fulfill({ json: current })
  })
  await page.route('**/api/app/triage/knn/demote', (r) => {
    current = { ...current, promoted: false, can_promote: true, can_demote: false, acting: false }
    return r.fulfill({ json: current })
  })
  await page.goto('/dashboard/needs-you')
}

test('an eligible head shows the numbers and a Promote button; promoting is recorded and can be taken back', async ({ page, owner }) => {
  void owner
  await open(page, shadow)
  const card = page.getByTestId('knn-promote')
  await expect(card).toContainText('Let the head decide who interrupts you?')
  await expect(card).toContainText('92% over 240 corrections in the last 30 days')
  await card.getByRole('button', { name: 'Promote' }).click()
  const on = page.getByTestId('knn-promoted')
  await expect(on).toContainText('a message that sounds like ones you called urgent is raised, never lowered')
  await expect(page.getByTestId('knn-promote')).toHaveCount(0)
  await on.getByRole('button', { name: 'Take it back' }).click()
  await expect(page.getByTestId('knn-promote')).toBeVisible()
})

test('a head that is not eligible shows only the shadow line and no Promote button', async ({ page, owner }) => {
  void owner
  await open(page, { ...shadow, n: 40, eligible: false, can_promote: false, text: 'Shadow: agrees with you 90% over 40 corrections, needs 200.' })
  await expect(page.getByTestId('knn-shadow')).toContainText('needs 200')
  await expect(page.getByRole('button', { name: 'Promote' })).toHaveCount(0)
})

test('raising switched off by the operator says so, and a head under the bar shows when it ends', async ({ page, owner }) => {
  void owner
  await open(page, { ...shadow, promoted: true, can_promote: false, can_demote: true, acting: false, raise_enabled: false, below_gate_since: '2026-10-20T00:00:00Z', demotes_in_days: 9 })
  const on = page.getByTestId('knn-promoted')
  await expect(on).toContainText('raising is switched off')
  await expect(on).toContainText('ends in 9 days')
})
