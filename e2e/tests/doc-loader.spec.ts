// The introduction's document loader: a card with the file or host, a scanning page, captions that move on by themselves
// (never a percentage), one announcement, a done state and an error state with Try again.
import { expect, test } from '../src/fixtures'
import { chooseFile, openIntroduction, openTab, textFile } from '../src/helpers/intro-sources'

const SHOTS = process.env.SHOT_DIR

test.beforeEach(async ({ page, api, owner }) => {
  void owner
  await openIntroduction(page, api)
  await openTab(page, 'Files & links')
})

async function hold(page: import('@playwright/test').Page, glob: string) {
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route(glob, async (route) => {
    await gate
    await route.continue()
  })
  return release
}

test('a file shows the loader with its name and type, then a done state', async ({ page }, info) => {
  const release = await hold(page, '**/api/app/intro/files')
  await chooseFile(page, textFile('resume.txt', 'I lead applied AI at PebbleRoad in Singapore.'))
  const loader = page.getByTestId('doc-loader')
  await expect(loader).toBeVisible()
  await expect(loader).toContainText('Reading resume.txt…')
  await expect(loader).toContainText('TXT')
  await expect(page.getByTestId('doc-stage')).toHaveText(/Uploading/)
  await expect(loader).not.toContainText('%')
  // One announcement: a single polite status inside the card; the captions are hidden from assistive technology.
  await expect(loader.getByRole('status')).toHaveCount(1)
  await expect(page.getByTestId('doc-stage')).toHaveAttribute('aria-hidden', 'true')
  await expect(page.getByTestId('doc-stage')).toHaveText(/Reading the pages/, { timeout: 5_000 })
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SHOT_PREFIX ?? 'after'}-loader-${info.project.name}.png` })
  release()
  await expect(loader).toHaveCount(0)
  await expect(page.getByTestId('doc-done')).toContainText(/resume\.txt/)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${process.env.SHOT_PREFIX ?? 'after'}-done-${info.project.name}.png` })
})

test('a link shows a link icon and the host name', async ({ page }) => {
  const release = await hold(page, '**/api/app/intro/links')
  await page.getByRole('textbox', { name: 'Link to a page about you' }).fill('https://example.com/about/me')
  await page.keyboard.press('Enter')
  const loader = page.getByTestId('doc-loader')
  await expect(loader).toContainText('example.com')
  await expect(page.getByTestId('doc-stage')).toHaveText(/Fetching the page/)
  release()
  await expect(loader).toHaveCount(0, { timeout: 15_000 })
})

test('a failed upload shows the error and Try again reads the file again', async ({ page }) => {
  let fail = true
  await page.route('**/api/app/intro/files', (route) => (fail ? route.abort('connectionrefused') : route.continue()))
  await chooseFile(page, textFile('resume.txt', 'I lead applied AI at PebbleRoad in Singapore.'))
  await expect(page.getByText(/Can't reach Superaide/)).toBeVisible()
  await expect(page.getByTestId('doc-loader')).toHaveCount(0)
  fail = false
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByTestId('doc-done')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0)
})

test.describe('reduced motion', () => {
  test.use({ reducedMotion: 'reduce' })
  test('the loader is a still icon with text', async ({ page }) => {
    const release = await hold(page, '**/api/app/intro/files')
    await chooseFile(page, textFile('resume.txt', 'I lead applied AI.'))
    const loader = page.getByTestId('doc-loader')
    await expect(loader).toContainText('Reading resume.txt…')
    await expect(loader.locator('.animate-spin')).toHaveCount(0)
    release()
  })
})
