// Skills: see what Ava can do, install one only after seeing its permissions, remove it.
import { expect, test } from '../../src/fixtures'

test('install Web research after reading its permissions, then remove it', async ({ page, owner }) => {
  void owner
  await page.goto('/dashboard')
  await page.getByRole('link', { name: 'Skills', exact: true }).click()
  await expect(page).toHaveURL(/\/dashboard\/skills$/)
  const skills = page.getByRole('list', { name: 'Skills' })
  const memory = skills.getByRole('listitem', { name: 'Memory' })
  await expect(memory.getByText('Installed')).toBeVisible()
  await expect(memory.getByText('Always on')).toBeVisible()

  const web = skills.getByRole('listitem', { name: 'Web research' })
  await expect(web.getByText(/About US\$0\.01/)).toBeVisible()
  await web.getByRole('button', { name: 'Install' }).click()
  const consent = web.getByRole('group', { name: 'Permissions for Web research' })
  await expect(consent.getByText('Read public information (the web)')).toBeVisible()
  await consent.getByRole('button', { name: 'Allow and install' }).click()
  await expect(web.getByText('Installed')).toBeVisible()

  await web.getByRole('button', { name: 'Remove' }).click()
  await expect(web.getByRole('button', { name: 'Install' })).toBeVisible()
})
