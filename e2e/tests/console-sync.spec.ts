// The operator console says the same as the app (issues #279, #280; the Telegram, Slack and brain lines need a fake Telegram/Anthropic and are covered in backend/tests/api/test_console_sync.py). Set SHOT=<dir> to keep screenshots.
import { expect, test } from '../src/fixtures'
import { ADMIN_TOKEN } from '../src/env'

const shot = (name: string) => (process.env.SHOT ? `${process.env.SHOT}/${name}.png` : undefined)

async function openConsole(page: import('@playwright/test').Page) {
  await page.goto(`/console?token=${ADMIN_TOKEN}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
}

test('the identity gate has no false failure after sign-up, and a forgotten note leaves the count', async ({ page, owner }) => {
  void owner
  await openConsole(page)
  await expect(page.getByText('0 links made without a verified step')).toBeVisible()
  const count = async () => Number((await page.getByText('Private memories').locator('xpath=following-sibling::dd[1]').innerText()).trim())
  const before = await count()
  await page.screenshot({ path: shot('console-before-forget') })
  await page.goto('/dashboard/notebook')
  await page.getByRole('button', { name: 'Forget this' }).first().click()
  await page.getByRole('button', { name: 'Forget', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Forget this' })).toHaveCount(before - 1)
  await openConsole(page)
  await expect.poll(count).toBe(before - 1)
  await page.screenshot({ path: shot('console-after-forget') })
})
