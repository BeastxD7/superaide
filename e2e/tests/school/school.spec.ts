// Ava School, the local half (#57): lessons an owner may share, never their data, by file.
// The off-by-default check runs everywhere. The live flow needs the page switched on, for example:
//   E2E_SCHOOL=1 E2E_SLOT=49 npx playwright test tests/school/school.spec.ts --project=desktop
import { SCHOOL } from '../../src/env'
import { expect, query, test } from '../../src/fixtures'

test.describe('Ava School, switched off (the default)', () => {
  test.skip(SCHOOL, 'only when Ava School is off')

  test('there is no link, the page says it is off and the API answers 404', async ({ page, api, owner }) => {
    void owner
    await page.goto('/dashboard')
        await expect(page.getByRole('link', { name: 'Learning' })).toHaveCount(0)
    await page.goto('/dashboard/school') // the old address redirects to Learning > Ava School
    await expect(page).toHaveURL(/\/dashboard\/learning\/school$/)
    await expect(page.getByText('This isn’t turned on yet.')).toBeVisible()
    expect((await api.get('/app/school')).status()).toBe(404)
  })
})

test.describe('Ava School, switched on', () => {
  test.skip(!SCHOOL, 'needs E2E_SCHOOL=1')

  test('write, refuse a name, agree, save a file, then read a file back', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard')
    await page.getByRole('link', { name: 'Learning' }).click()
    await page.getByRole('link', { name: 'Open Ava School' }).click()
    await expect(page.getByRole('heading', { name: 'Ava School' })).toBeVisible()

    // A lesson that names the owner is refused, with the reason.
    await page.getByLabel('One sentence of advice for any assistant').fill('Tell Ananya first when a supplier is late')
    await page.getByRole('button', { name: 'Add lesson' }).click()
    await expect(page.getByText(/mentions a name Ava knows \(ananya\)/)).toBeVisible()
    expect((await query<{ n: number }>('SELECT count(*)::int AS n FROM lesson'))[0].n).toBe(0)

    await page.getByLabel('One sentence of advice for any assistant').fill('When I say urgent I mean money problems, not deadlines.')
    await page.getByRole('button', { name: 'Add lesson' }).click()
    await expect(page.getByText('When I say urgent I mean money problems, not deadlines.')).toBeVisible()

    await page.getByLabel('I agree to share this lesson').click()
    await expect(page.getByLabel('I agree to share this lesson')).toBeChecked()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: /Save the 1 lesson I agreed to share/ }).click()
    const file = await (await download).path()
    const fs = await import('node:fs')
    const doc = JSON.parse(fs.readFileSync(file, 'utf8'))
    expect(doc.lessons).toEqual([{ text: 'When I say urgent I mean money problems, not deadlines.', role: 'personal assistant' }])
    expect(Object.keys(doc).sort()).toEqual(['format', 'lessons', 'sha256'])

    // Another assistant's file: read it back (after deleting ours so it is not a duplicate).
    await page.getByRole('button', { name: /Delete lesson/ }).click()
    await expect(page.getByText('No lessons yet.')).toBeVisible()
    await page.locator('#lesson-file').setInputFiles(file)
    await expect(page.getByText('1 lesson added and waiting for you.')).toBeVisible()
    await expect(page.getByText('Imported, waiting for you')).toBeVisible()
    await page.getByRole('button', { name: 'Keep this lesson' }).click()
    await expect(page.getByText('Imported', { exact: true })).toBeVisible()
  })
})
