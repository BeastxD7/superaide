// #78 FE-22: the call listens in the browser's language by default, shows which language that is, and lets the person pick
// another (kept on this browser).
import { expect, test } from '../src/fixtures'
import { fakeRecognition, openIntroduction, quickVoice, startCall } from '../src/helpers/intro-sources'

test.use({ ownerState: 'hired', locale: 'en-GB' })

async function listeningLang(page: import('@playwright/test').Page) {
  await expect.poll(() => page.evaluate(() => (window as unknown as { __recognizer?: { lang: string } }).__recognizer?.lang ?? ''), { timeout: 15_000 }).not.toBe('')
  return page.evaluate(() => (window as unknown as { __recognizer: { lang: string } }).__recognizer.lang)
}

test('the call listens in the browser language, shows it, and remembers a different choice', async ({ page, api, owner }, info) => {
  void owner
  await fakeRecognition(page)
  await quickVoice(page)
  await openIntroduction(page, api)
  const dialog = await startCall(page)
  const picker = dialog.getByLabel('Language the microphone listens in')
  await expect(picker).toBeVisible()
  await expect(picker).toHaveValue('en-GB')
  await expect(dialog.getByText('Listening in')).toBeVisible()
  expect(await listeningLang(page)).toBe('en-GB')
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-lang-${info.project.name}.png` })

  await picker.selectOption('fr-FR')
  expect(await page.evaluate(() => localStorage.getItem('superaide-speech-lang'))).toBe('fr-FR')
  await page.reload()
  await openIntroduction(page, api)
  const again = await startCall(page)
  await expect(again.getByLabel('Language the microphone listens in')).toHaveValue('fr-FR')
  expect(await listeningLang(page)).toBe('fr-FR')
})
