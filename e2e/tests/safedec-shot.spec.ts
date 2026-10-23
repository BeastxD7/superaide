import fs from 'node:fs'
import { test } from '../src/fixtures'

test.use({ ownerState: 'hired' })
test('shot', async ({ page, owner }) => {
  const f = process.env.SHOT_URL_FILE!
  if (process.env.SHOT_MODE === 'after') {
    const r = await page.request.put('/api/app/principal-card', {
      data: { display_name: 'A. Tan', can: ['Propose times', 'Pass on messages'], cannot: ['Agree prices', 'Sign anything', 'Pay anyone'] },
      headers: { 'X-Requested-With': 'superaide-e2e' },
    })
    const j = await r.json()
    fs.writeFileSync(f, new URL(j.preview).pathname)
  }
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(fs.readFileSync(f, 'utf8'))
  await page.waitForTimeout(500)
  await page.screenshot({ path: process.env.SHOT_PATH! })
})
