// Issue #75: when the introduction cannot be loaded, /meet says so and offers Try again
// (it used to say "All set" with an endless "Ava is typing…").
import { expect, test } from '../src/fixtures'

test.use({ ownerState: 'hired' })

test.describe('the introduction when loading fails', () => {
  test('says it could not load, offers Try again, and recovers', async ({ page, owner }) => {
    void owner
    let failing = true
    await page.route('**/api/app/onboarding', (route) =>
      failing ? route.fulfill({ status: 502, contentType: 'application/json', body: '{"detail":"Bad gateway"}' }) : route.continue(),
    )
    await page.goto('/meet')
    await expect(page.getByText("Couldn't load")).toBeVisible()
    await expect(page.getByText('All set')).toHaveCount(0)
    await expect(page.getByText(/is typing…/)).toHaveCount(0)
    const retry = page.getByRole('button', { name: 'Try again' })
    await expect(retry).toBeVisible()
    await expect(page.getByRole('button', { name: 'Leave' })).toBeVisible()
    failing = false
    await retry.click()
    await expect(page.getByText(/Getting to know you\. Step 1 of/)).toBeVisible()
    await expect(page.getByText("Couldn't load")).toHaveCount(0)
  })
})
