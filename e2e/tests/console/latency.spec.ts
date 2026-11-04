// #412: the console's Latency tab shows p50 and p95 per step of a reply, from numbers only.
import type { Page } from '@playwright/test'
import { ADMIN_TOKEN } from '../../src/env'
import { expect, test } from '../../src/fixtures'
import { Admin } from '../../src/helpers/workspace'

const CANARY = 'LATENCY-CANARY-4417'

async function openLatency(page: Page): Promise<void> {
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await page.getByRole('navigation', { name: 'Sections' }).getByRole('button', { name: 'Latency', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Latency' })).toBeVisible()
}

test.describe('console: latency', () => {
  test('after a chat turn the tab lists every step with p50 and p95, and none of the text', async ({ page, api, owner }, info) => {
    void owner
    await api.ok(api.post('/app/chat', { text: `hello ${CANARY}` }))
    await openLatency(page)
    const table = page.getByRole('region', { name: /Milliseconds per step/ })
    await expect(table).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'All p50' })).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'All p95' })).toBeVisible()
    for (const step of ['Whole turn', 'Who is it', 'Audience gate', 'Channel send']) {
      await expect(table.getByRole('cell', { name: step, exact: true })).toBeVisible()
    }
    await expect(page.getByText(/^web p50$/)).toBeVisible() // a column for the channel the turn came in on
    await expect(page.getByText(CANARY)).toHaveCount(0)
    if (process.env.SHOT_DIR)
      await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-latency-${info.project.name}.png` })
  })

  test('the window buttons change what is asked, and an empty window says so', async ({ page, api, owner }) => {
    void owner
    void api
    await openLatency(page)
    await expect(page.getByText('No turns in this window')).toBeVisible()
    const asked = page.waitForRequest((r) => r.url().includes('/api/latency?hours=168'))
    await page.getByRole('button', { name: 'Last week' }).click()
    await asked
    await expect(page.getByRole('button', { name: 'Last week' })).toHaveAttribute('aria-pressed', 'true')
  })

  test('the latency route needs the operator token and takes only a sane window', async ({ page, api, owner }) => {
    void owner
    void api
    for (const admin of [new Admin(page.request, null), new Admin(page.request, 'wrong-token')]) {
      expect((await admin.get('/latency')).status()).toBe(401)
    }
    const ok = new Admin(page.request)
    expect((await ok.get('/latency')).status()).toBe(200)
    expect((await ok.get('/latency?hours=0')).status()).toBe(422)
    expect((await ok.get('/latency?hours=100000')).status()).toBe(422)
  })
})
