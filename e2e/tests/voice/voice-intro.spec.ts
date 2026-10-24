// The introduction's voice call, v2 (ADR 0012). Runs with E2E_VOICE=1 (its own slot):
//   E2E_VOICE=1 E2E_SLOT=81 npx playwright test tests/voice/voice-intro.spec.ts
// The microphone is a WAV of someone speaking (Chromium's fake device loops it), so the
// on-device voice detector really hears speech start and stop. The server's speech-to-text
// and voice are fakes (VOICE_ASR=fake, VOICE_TTS=fake): no vendor is called and nothing is heard,
// as Chromium also runs with --mute-audio and speechSynthesis is silenced by the fixtures.
import { fileURLToPath } from 'node:url'
import { query } from '../../src/db'
import { VOICE } from '../../src/env'
import { expect, test } from '../../src/fixtures'
import { openIntroduction, openTab } from '../../src/helpers/intro-sources'

const WAV = fileURLToPath(new URL('../fixtures/voice/owner-speaks.wav', import.meta.url))
// What the fake speech-to-text hears: one of its scripted answers, in turn (backend voice/providers.py
// FAKE_ANSWERS). The API process keeps its place across tests, so any of them may come first.
const HEARD = /I lead applied AI|My wife Priya|Message me on Telegram|never share my phone number/

test.skip(!VOICE, 'set E2E_VOICE=1 (the API needs VOICE_INTRO_V2 and fake speech services)')
test.use({ ownerState: 'hired' })
test.use({
  launchOptions: {
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${WAV}`,
      '--mute-audio',
      '--autoplay-policy=no-user-gesture-required',
    ],
  },
})

test.describe('voice call v2', () => {
  test.beforeEach(async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
  })

  test('start → the detector hears speech → transcript → reply captions → end → confirm what was learned', async ({ page }) => {
    await openTab(page, 'Video call')
    await page.getByRole('button', { name: 'Start the call' }).click()
    const dialog = page.getByRole('dialog', { name: 'Voice call with Ava' })
    await expect(dialog).toBeVisible()

    // Ava speaks first (checked in the conversation below: the microphone may answer her quickly).
    // Nobody presses anything: the detector notices the speech, and the transcript comes back.
    await expect(dialog.getByTestId('you-caption')).toContainText(HEARD, { timeout: 30_000 })
    // Ava's reply is captioned (the scripted interviewer moves on to the next topic).
    await expect(dialog.getByTestId('ava-caption')).toContainText('What are you focused on at the moment', { timeout: 20_000 })
    const latency = await page.evaluate(() => (window as unknown as { __voiceLatency?: number[] }).__voiceLatency ?? [])
    expect(latency.length).toBeGreaterThan(0)
    test.info().annotations.push({ type: 'latency (speech end → first sound, ms)', description: latency.join(', ') })
    console.log(`voice latency ms: ${latency.join(', ')}`)

    // End the call: nothing is saved until the owner confirms.
    await dialog.getByRole('button', { name: 'End call' }).click()
    const summary = dialog.getByTestId('voice-summary')
    await expect(summary).toBeVisible()
    const first = summary.getByRole('textbox', { name: 'Fact 1' })
    await expect(first).toHaveValue(/^On the intro call, Ananya Menon said: /)
    expect(await query("SELECT 1 FROM memory_item WHERE source = 'intro_call'")).toHaveLength(0)

    await first.fill('Ananya leads applied AI at a design consultancy in Singapore.')
    await summary.getByRole('button', { name: 'Save to my notebook' }).click()
    await expect(dialog).toBeHidden()
    const saved = await query<{ content: string }>("SELECT content FROM memory_item WHERE source = 'intro_call'")
    expect(saved.map((r) => r.content)).toContain('Ananya leads applied AI at a design consultancy in Singapore.')
    await expect(page.getByText(/Thanks for the call. I've saved \d+ things? about you/)).toBeVisible()
    await expect(page.getByText(/^Hello Ananya, I'm Ava\. Thank you for making the time/)).toBeVisible()
  })

  test('typing instead works without the microphone, and discarding saves nothing', async ({ page }) => {
    await openTab(page, 'Video call')
    await page.getByRole('button', { name: 'Start the call' }).click()
    const dialog = page.getByRole('dialog', { name: 'Voice call with Ava' })
    await dialog.getByRole('button', { name: 'Mute microphone' }).click()
    await expect(dialog.getByTestId('ava-caption')).toContainText('Hello Ananya', { timeout: 20_000 })
    await dialog.getByRole('button', { name: 'Type instead' }).click()
    await dialog.getByRole('textbox', { name: 'Type your answer' }).fill('I run a small design studio.')
    await dialog.getByRole('button', { name: 'Send' }).click()
    await expect(dialog.getByTestId('you-caption')).toHaveText('I run a small design studio.')
    await expect(dialog.getByTestId('ava-caption')).toContainText('What are you focused on', { timeout: 20_000 })
    await dialog.getByRole('button', { name: 'End call' }).click()
    await dialog.getByRole('button', { name: "Don't save anything" }).click()
    await expect(dialog).toBeHidden()
    expect(await query("SELECT 1 FROM memory_item WHERE source = 'intro_call'")).toHaveLength(0)
  })
})
