// The document loader on a phone: it fits the screen, with a long file name and with a link.
import { expect, test } from '../src/fixtures'
import { chooseFile, fileRow, hasNoSidewaysScroll, insideViewport, openIntroduction, openTab, textFile } from '../src/helpers/intro-sources'

test.use({ ownerState: 'hired', viewport: { width: 360, height: 740 } })

test('the loader fits a 360px screen, even with a long file name', async ({ page, api, owner }, info) => {
  void owner
  await openIntroduction(page, api)
  await openTab(page, 'Files & links')
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/app/intro/files', async (route) => {
    await gate
    await route.continue()
  })
  const long = 'Ananya-Menon-Curriculum-Vitae-2026-final-final-v3-for-the-assistant.txt'
  await chooseFile(page, textFile(long, 'I lead applied AI at PebbleRoad in Singapore.'))
  const loader = page.getByTestId('doc-loader')
  await expect(loader).toBeVisible()
  await insideViewport(page, loader, 'the loader')
  await hasNoSidewaysScroll(page)
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-loader-${info.project.name}.png` })
  release()
  // The green "doc-done" notice is transient (the step reloads right after the upload and can drop it before
  // the browser checks), so assert on the durable end state: the loader is gone and the file is listed.
  await expect(loader).toHaveCount(0)
  const row = fileRow(page, long)
  await expect(row).toBeVisible()
  await expect(row).toContainText(long)
  await insideViewport(page, row, 'the saved file row')
  // If the notice happens to still be showing, it must fit too; its absence is fine.
  const done = page.getByTestId('doc-done')
  if (await done.isVisible()) await insideViewport(page, done, 'the done state')
  await hasNoSidewaysScroll(page)
})
