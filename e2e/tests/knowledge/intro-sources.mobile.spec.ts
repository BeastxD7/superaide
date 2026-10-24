// The introduction's files list and video call on a phone (Pixel 7).
import { expect, test } from '../../src/fixtures'
import {
  CALL_QUESTIONS,
  chooseFile,
  fileRow,
  hasNoSidewaysScroll,
  insideViewport,
  introTabs,
  leaveCall,
  openIntroduction,
  openTab,
  pdf,
  quickVoice,
  startCall,
  textFile,
  typeAnswer,
} from '../../src/helpers/intro-sources'

test.use({ ownerState: 'hired' })

test.beforeEach(async ({ page, api, owner }) => {
  void owner
  await quickVoice(page)
  await openIntroduction(page, api)
})

test('the three intro tabs fit across a phone screen', async ({ page }) => {
  for (const name of ['Tell me here', 'Files & links', 'Video call']) {
    await insideViewport(page, page.getByRole('tab', { name }), `the "${name}" tab`)
  }
  await hasNoSidewaysScroll(page)
})

test('the intro tab labels are readable in full on a phone', async ({ page }) => {
  // F10, fixed by 4d1b3f6: phones get short tab labels (features/onboarding/steps.tsx).
  for (const name of ['Tell me here', 'Files & links', 'Video call']) {
    const cut = await page.getByRole('tab', { name }).evaluate((tab) =>
      Array.from(tab.querySelectorAll('span')).some((s) => s.scrollWidth > s.clientWidth),
    )
    expect(cut, `the "${name}" label is cut off`).toBe(false)
  }
})

test('the files list fits a phone screen, even with a long file name', async ({ page }) => {
  await openTab(page, 'Files & links')
  const long = 'Ananya-Menon-Curriculum-Vitae-2026-final-final-v3-for-the-assistant-with-a-very-long-name.txt'
  await chooseFile(page, textFile(long, 'I lead applied AI at PebbleRoad in Singapore.'))
  await expect(fileRow(page, long)).toBeVisible()
  await chooseFile(page, pdf(['Ananya Menon', 'Leads applied AI at PebbleRoad in Singapore.']))
  await expect(fileRow(page, 'resume.pdf')).toBeVisible()

  for (const title of [long, 'resume.pdf']) {
    const row = fileRow(page, title)
    await row.scrollIntoViewIfNeeded()
    await insideViewport(page, row, `the row for ${title}`)
    const remove = page.getByRole('button', { name: `Remove ${title}` })
    await insideViewport(page, remove, `the remove button for ${title}`)
    await expect(row).toContainText('Saved, not read yet')
  }
  // A refusal message fits too.
  await chooseFile(page, { name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from('png') })
  const refusal = page.getByText("That file type isn't supported. Upload a PDF, Word (.docx), .txt or .md file.")
  await expect(refusal).toBeVisible()
  await insideViewport(page, refusal, 'the refusal message')
  await insideViewport(page, page.getByRole('button', { name: 'Continue' }), 'the Continue button')
  await hasNoSidewaysScroll(page)

  // Removing works with a tap.
  await page.getByRole('button', { name: `Remove ${long}` }).tap()
  await expect(fileRow(page, long)).toHaveCount(0)
})

test('the call screen fits a phone screen and can be answered by typing', async ({ page }) => {
  await expect(introTabs(page)).toBeVisible()
  const dialog = await startCall(page)
  await expect(dialog.getByText(CALL_QUESTIONS[0], { exact: true })).toBeVisible()
  await hasNoSidewaysScroll(page)

  for (const name of ['Leave the call', 'Turn captions off']) {
    await insideViewport(page, dialog.getByRole('button', { name }), `the "${name}" button`)
  }
  const you = dialog.getByRole('region', { name: 'You' })
  await insideViewport(page, you, 'your camera tile')
  await insideViewport(page, dialog.getByText(CALL_QUESTIONS[0], { exact: true }), 'the question')

  await typeAnswer(dialog, CALL_QUESTIONS[0], 'I lead applied AI at PebbleRoad in Singapore.')
  await expect(dialog.getByText(CALL_QUESTIONS[1], { exact: true })).toBeVisible()
  const box = dialog.getByRole('textbox', { name: 'Type your answer' })
  await insideViewport(page, box, 'the answer box')
  await insideViewport(page, dialog.getByRole('button', { name: 'Send', exact: true }), 'the Send button')
  // The answer box, Send and Leave are all on screen together, without scrolling.
  await expect(box).toBeInViewport()
  await expect(dialog.getByRole('button', { name: 'Send', exact: true })).toBeInViewport()
  await expect(dialog.getByRole('button', { name: 'Leave the call' })).toBeInViewport()
  await hasNoSidewaysScroll(page)

  // The leave confirmation fits as well.
  await dialog.getByRole('button', { name: 'Leave the call' }).tap()
  const confirm = dialog.getByRole('group', { name: 'Confirm leaving the call' })
  await expect(confirm).toBeVisible()
  await insideViewport(page, confirm, 'the leave confirmation')
  await confirm.getByRole('button', { name: 'Stay' }).tap()
  await leaveCall(page, dialog)
})
