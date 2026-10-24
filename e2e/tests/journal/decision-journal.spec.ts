// "Ava keeps score" (#53): opt-in decision journal, resolve predictions, calibration after ten, off deletes it.
import { expect, test } from '../../src/fixtures'

test.describe('decision journal', () => {
  test('is off until turned on, and the notebook links to it', async ({ page, owner }) => {
    void owner
    await page.goto('/dashboard/notebook')
    await page.getByRole('link', { name: /Decision journal/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Decision journal' })).toBeVisible()
    await expect(page.getByText('Off for now')).toBeVisible()
    await expect(page.getByRole('form', { name: 'Add a prediction' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Turn on the journal' }).click()
    await expect(page.getByRole('form', { name: 'Add a prediction' })).toBeVisible()
    await expect(page.getByText('0 of 10 resolved')).toBeVisible()
  })

  test('add a prediction, mark it, and see it move to Checked', async ({ page, owner, api }) => {
    void owner
    await api.post('/app/journal/settings', { enabled: true })
    await page.goto('/dashboard/notebook/journal')
    const form = page.getByRole('form', { name: 'Add a prediction' })
    const add = form.getByRole('button', { name: 'Add to journal' })
    await expect(add).toBeDisabled()
    await form.getByLabel('What do you expect to happen?').fill('The beta ships by 5 November')
    await form.getByLabel('How sure').selectOption('90')
    await add.click()
    const item = page.getByRole('listitem').filter({ hasText: 'The beta ships by 5 November' })
    await expect(item).toContainText('90% sure')
    await item.getByRole('button', { name: "It didn't" }).click()
    await expect(page.getByRole('heading', { name: 'Checked' })).toBeVisible()
    await expect(page.getByText('Didn’t')).toBeVisible()
    await expect(page.getByText('1 of 10 resolved')).toBeVisible()
    // Adding the same thing twice is refused with a reason.
    await form.getByLabel('What do you expect to happen?').fill('Another thing happens')
    await add.click()
    await expect(page.getByRole('listitem').filter({ hasText: 'Another thing happens' })).toBeVisible()
    await form.getByLabel('What do you expect to happen?').fill('Another thing happens')
    await add.click()
    await expect(page.getByRole('alert').filter({ hasText: "Couldn't update" })).toContainText('already in your journal')
  })

  test('after ten resolved it shows calibration and coaching; off deletes everything', async ({ page, owner, api }) => {
    void owner
    await api.post('/app/journal/settings', { enabled: true })
    for (let i = 0; i < 10; i++) {
      const p = await api.ok<{ id: string }>(
        api.post('/app/journal/predictions', { statement: `Deadline number ${i} will be met`, confidence_pct: 90, topic: 'timeline' }),
      )
      await api.ok(api.post(`/app/journal/predictions/${p.id}/resolve`, { came_true: i < 5 }))
    }
    await page.goto('/dashboard/notebook/journal')
    await expect(page.getByText('Right 5 of 10 times (50%)')).toBeVisible()
    await expect(page.getByRole('list', { name: 'Confidence against results' })).toContainText('said 90%')
    await expect(page.getByText(/On timelines you're right 50% of the time when you say you're 90% sure/)).toBeVisible()

    await page.getByRole('button', { name: 'Turn off and delete the journal' }).click()
    await page.getByRole('button', { name: 'Delete the journal' }).click()
    await expect(page.getByText('Off for now')).toBeVisible()
    const after = await api.ok<{ predictions: unknown[] }>(api.get('/app/journal'))
    expect(after.predictions).toEqual([])
  })

  test('an off journal refuses writes, and signed out is refused', async ({ api, owner }) => {
    void owner
    expect((await api.post('/app/journal/predictions', { statement: 'Something happens', confidence_pct: 70 })).status()).toBe(409)
    expect((await api.postWithoutCsrfHeader('/app/journal/settings', { enabled: true })).status()).toBeGreaterThanOrEqual(400)
  })
})
