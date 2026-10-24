// The emailed verify and reset links under `next dev`, where React runs every effect twice (issue #70).
// Only the "dev" project runs this file (E2E_DEV=1), because the production build never double-runs effects:
//   E2E_DEV=1 E2E_SLOT=91 npx playwright test tests/web-shell/fe-links.dev.spec.ts --project=dev
import { createHash, randomBytes } from 'node:crypto'
import { expect, query, test } from '../../src/fixtures'

async function emailLink(email: string, purpose: 'verify_email' | 'reset_password'): Promise<string> {
  const token = randomBytes(32).toString('base64url')
  const hash = createHash('sha256').update(token).digest('hex')
  await query(
    `INSERT INTO email_token (id, user_id, purpose, email_normalized, token_hash, expires_at, created_at)
     SELECT gen_random_uuid(), id, $2, email_normalized, $3, now() + interval '30 minutes', now()
     FROM user_account WHERE email_normalized = $1`,
    [email.toLowerCase(), purpose, hash],
  )
  return token
}

test('a verification link works the first and only time it is opened in development', async ({ page, owner, api }) => {
  await api.post('/auth/logout')
  const token = await emailLink(owner.email, 'verify_email')
  await page.goto(`/verify-email#token=${token}`)
  await expect(page.getByText('Your email address is confirmed. Thank you.')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('missing its code')).toHaveCount(0)
})

test('a reset link shows the new-password form in development', async ({ page, owner, api }) => {
  await api.post('/auth/logout')
  const token = await emailLink(owner.email, 'reset_password')
  await page.goto(`/reset-password#token=${token}`)
  await expect(page.getByLabel('New password')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('missing its code')).toHaveCount(0)
})
