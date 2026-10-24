// #433: what Ava may read in Gmail is a visible, owner-controlled setting on the Gmail card.
import AxeBuilder from '@axe-core/playwright'
import { expect, query, test } from '../../src/fixtures'
import { plantSecret } from '../../src/helpers/vault'

const CLIENT = 'abc.apps.googleusercontent.com'

async function connectGmail(api: { ok: <T>(p: Promise<unknown>) => Promise<T>; get: (u: string) => Promise<unknown>; post: (u: string, b?: unknown) => Promise<unknown> }) {
  await plantSecret('google', 'client_id', CLIENT)
  await plantSecret('google', 'client_secret', 'GOCSPX-secretsecret')
  await plantSecret('google', 'gmail_refresh_token', 'rt-1234567890abcdef')
  await query(
    `UPDATE integration_secret SET meta = $1::jsonb WHERE name = 'gmail_refresh_token'`,
    [JSON.stringify({ scopes: ['https://www.googleapis.com/auth/gmail.readonly'], client_id: CLIENT, address: 'ananya@example.com' })],
  )
  const cards = await api.ok<{ id: string; permissions: { id: string }[] }[]>(api.get('/app/skills') as Promise<unknown>)
  const gmail = cards.find((c) => c.id === 'superaide/gmail')!
  await api.ok(api.post('/app/skills/superaide/gmail/install', { accepted: gmail.permissions.map((p) => p.id) }) as Promise<unknown>)
}

test('the Gmail card shows every reading rule, saves a change, and asks for the password before codes', async ({ page, api, owner }, info) => {
  void owner
  await connectGmail(api as never)
  await page.goto('/dashboard/capabilities')
  const group = page.getByRole('group', { name: 'What Ava may read in Gmail' })
  await expect(group).toBeVisible()
  for (const label of [
    'Payment and transaction emails: previews',
    'Numbers in emails',
    'One-time codes and password resets',
    'How much of a message Ava reads',
    'Search size',
    'Thread depth',
    'How far back Ava looks',
    'Never read these senders, domains or labels',
    'Always allow these senders',
  ])
    await expect(group.getByText(label, { exact: true })).toBeVisible()
  await expect(group.getByLabel('One-time codes and password resets')).toHaveValue('never')

  await group.getByLabel('Payment and transaction emails: previews').selectOption('show')
  await expect(group.getByText('Payment and transaction emails: previews saved.')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('group', { name: 'What Ava may read in Gmail' }).getByLabel('Payment and transaction emails: previews')).toHaveValue('show')

  await group.getByLabel('Never read these senders, domains or labels').fill('@clinic.example\nlabel:Taxes')
  await group.getByRole('button', { name: 'Save list' }).first().click()
  await expect(group.getByText('Never-read list saved.')).toBeVisible()

  await group.getByLabel('One-time codes and password resets').selectOption('read')
  await expect(page.getByRole('alertdialog', { name: 'Confirm' })).toContainText('Enter your password')
  await page.getByLabel('Your password').fill('definitely-not-it')
  await page.getByRole('button', { name: 'Turn on' }).click()
  await expect(page.getByText("Your password isn't right.")).toBeVisible()
  await expect(group.getByLabel('One-time codes and password resets')).toHaveValue('never')
  await group.getByLabel('Search size').scrollIntoViewIfNeeded()
  const axe = await new AxeBuilder({ page }).include('#gmail-read').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([])
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/433-${info.project.name}.png` })
})
