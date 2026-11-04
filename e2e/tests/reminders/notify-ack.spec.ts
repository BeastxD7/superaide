// A push that never reached the owner's phone is listed on Home; "I have seen this" stops the retries and clears it (#491).
import { expect, query, test } from '../../src/fixtures'

const seed = (state: string, channel: string, text: string) =>
  query(
    `INSERT INTO notification_delivery (id, tenant_id, agent_id, channel, text, meta, state, attempts, last_error)
     SELECT gen_random_uuid(), tenant_id, agent_id, $1, $2, '{}'::jsonb, $3, 4, 'Telegram could not be reached.'
     FROM person LIMIT 1`,
    [channel, text, state],
  )

test.describe('not-delivered notifications', () => {
  test('the owner marks a notification as seen and it leaves Home', async ({ page, owner }) => {
    void owner
    await seed('failed', 'telegram', 'Meera needs you within 5 minutes')
    await seed('queued', 'slack', 'Rahul asked for a call back')
    await page.goto('/dashboard')
    const card = page.getByTestId('undelivered-card')
    await expect(card.getByText('2 notifications did not reach you')).toBeVisible()
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/ack-1-before.png` : undefined })

    await card.getByRole('button', { name: /Mark as seen: Meera needs you/ }).click()
    await expect(card.getByText('1 notification did not reach you')).toBeVisible()
    await expect(card.getByText('Meera needs you within 5 minutes')).toHaveCount(0)
    await expect(card.getByText('Rahul asked for a call back')).toBeVisible()
    await page.screenshot({ path: process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/ack-2-after.png` : undefined })

    await card.getByRole('button', { name: /Mark as seen: Rahul/ }).click()
    await expect(page.getByTestId('undelivered-card')).toHaveCount(0)
    await page.reload()
    await expect(page.getByTestId('undelivered-card')).toHaveCount(0)
  })

  test('marking a notification that does not exist as seen is refused', async ({ api, owner }) => {
    void owner
    const r = await api.post('/app/notifications/00000000-0000-4000-8000-000000000000/ack')
    expect(r.status()).toBe(404)
  })
})
