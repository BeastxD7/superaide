// Capabilities (skills in code): see what Ava can do, install one only after seeing its permissions, remove it.
import { expect, test } from '../../src/fixtures'

test('install Web research after reading its permissions, then remove it', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('link', { name: 'Capabilities', exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard\/capabilities$/)
  await expect(page.getByRole('heading', { name: 'Capabilities', level: 1 })).toBeVisible()
  const skills = page.getByRole('list', { name: 'Capabilities' })
  const memory = skills.getByRole('listitem', { name: 'Memory' })
  await expect(memory.getByText('Installed')).toBeVisible()
  await expect(memory.getByText('Always on')).toBeVisible()

  const web = skills.getByRole('listitem', { name: 'Web research' })
  await expect(web.getByText("About US$0.01 each time it's used")).toBeVisible()
  await web.getByRole('button', { name: 'Install' }).click()
  const consent = web.getByRole('group', { name: 'Permissions for Web research' })
  await expect(consent.getByText('Read public information (the web)')).toBeVisible()
  await consent.getByRole('button', { name: 'Allow and install' }).click()
  await expect(web.getByText('Installed')).toBeVisible()

  await web.getByRole('button', { name: 'Remove' }).click()
  await expect(web.getByRole('button', { name: 'Install' })).toBeVisible()
})

test('the old Skills address lands on Capabilities', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard/skills')
  await expect(page).toHaveURL(/\/dashboard\/capabilities$/)
  await expect(page.getByRole('heading', { name: 'Capabilities', level: 1 })).toBeVisible()
})
