// Issue #76 FE-19: a keyboard user can see which field has focus. Fields that remove the browser's outline
// must draw their own ring (a box-shadow), not only change a border colour.
import { expect, test } from '../../src/fixtures'

test('the sign-in fields draw a focus ring', async ({ page }) => {
  await page.goto('/login')
  for (const label of ['Email', 'Password']) {
    const field = page.getByLabel(label, { exact: false }).first()
    await field.focus()
    const shadow = await field.evaluate((el) => getComputedStyle(el).boxShadow)
    expect(shadow, `${label} shows a focus ring`).not.toBe('none')
  }
})
