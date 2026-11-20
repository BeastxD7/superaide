// Voice notes and photos (#501, #511): the owner chooses which connected service reads them, on the AI model page.
// A tiny OpenAI-compatible server on this machine stands in for a local Whisper / vision server: nothing leaves the
// machine and nothing is spent. The offline responder (the e2e API runs with FAKE_LLM) covers the no-network path.
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import type { Page } from '@playwright/test'
import { expect, query, test } from '../../src/fixtures'
import { goToDashboardPage } from '../../src/helpers/workspace'

const shot = (name: string) => (process.env.SHOT_DIR ? `${process.env.SHOT_DIR}/${process.env.SHOT_PREFIX ?? 'after'}-${name}.png` : `test-results/${name}.png`)

function localServer(failAudio = false): Promise<{ server: Server; url: string; seen: string[] }> {
  const seen: string[] = []
  const server = createServer((req, res) => {
    seen.push(`${req.method} ${req.url}`)
    if (req.url?.endsWith('/audio/transcriptions') && failAudio) {
      res.writeHead(500).end('internal secret detail')
      return
    }
    res.writeHead(200, { 'content-type': 'application/json' })
    if (req.url?.endsWith('/models')) {
      res.end(JSON.stringify({ object: 'list', data: [{ id: 'whisper-local', object: 'model' }, { id: 'llava', object: 'model' }] }))
    } else if (req.url?.endsWith('/audio/transcriptions')) {
      res.end(JSON.stringify({ text: 'hello from a test', duration: 0.5 }))
    } else if (req.url?.endsWith('/chat/completions')) {
      res.end(JSON.stringify({ id: 'c1', choices: [{ message: { content: 'a grey square' } }], usage: { prompt_tokens: 50, completion_tokens: 4 } }))
    } else {
      res.writeHead(404).end()
    }
  })
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`, seen })),
  )
}

async function openSection(page: Page) {
  await page.goto('/dashboard/chat')
  await goToDashboardPage(page, 'AI model')
  const section = page.getByRole('region', { name: 'Voice notes and photos' })
  await expect(section).toBeVisible()
  return section
}

test.describe('Voice notes and photos settings', () => {
  test('both start off and say nothing is sent; with no service connected it says what to do @smoke', async ({ page, owner }) => {
    void owner
    await page.setViewportSize({ width: 1280, height: 800 })
    const section = await openSection(page)
    await expect(page.getByTestId('media-status-transcribe')).toHaveText('Off. Nothing is sent.')
    await expect(page.getByTestId('media-status-vision')).toHaveText('Off. Nothing is sent.')
    // Only connected services are offered, and Claude never for voice notes.
    await expect(section.getByRole('group', { name: 'Voice notes (speech to text)' }).getByLabel('Service')).not.toContainText('Claude')
    await section.scrollIntoViewIfNeeded()
    await page.screenshot({ path: shot('media-off-1280') })
    await page.setViewportSize({ width: 390, height: 844 })
    await expect(section).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: shot('media-off-390') })
  })

  test('choose the offline responder, test it, then turn it off', async ({ page, owner }) => {
    void owner
    const section = await openSection(page)
    const voice = section.getByRole('group', { name: 'Voice notes (speech to text)' })
    await voice.getByLabel('Service').selectOption({ label: 'Offline test responder' })
    await voice.getByLabel('Model').fill('fake')
    await voice.getByRole('button', { name: 'Test' }).click()
    await expect(voice.getByText('It works.')).toBeVisible()
    await voice.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('media-status-transcribe')).toHaveText('On. This one is free.')
    expect(await query('SELECT role, provider, model FROM model_role WHERE role = $1', ['transcribe'])).toEqual([
      { role: 'transcribe', provider: 'fake', model: 'fake' },
    ])
    await voice.getByRole('button', { name: 'Turn off' }).click()
    await expect(page.getByTestId('media-status-transcribe')).toHaveText('Off. Nothing is sent.')
    expect(await query('SELECT role FROM model_role')).toEqual([])
  })

  test('a local Whisper server: connect it, choose it for voice notes, the test calls its transcription address @smoke', async ({ page, owner }) => {
    void owner
    const { server, url, seen } = await localServer()
    try {
      await page.setViewportSize({ width: 1280, height: 800 })
      await page.goto('/dashboard/chat')
      await goToDashboardPage(page, 'AI model')
      const custom = page.getByRole('listitem', { name: 'Other OpenAI-compatible service', exact: true })
      await custom.getByRole('button', { name: 'Connect' }).click()
      await custom.getByLabel('Address (base URL)').fill(url)
      await custom.getByRole('button', { name: 'Save' }).click()
      await expect(custom.getByText('Connected', { exact: true })).toBeVisible()

      const section = page.getByRole('region', { name: 'Voice notes and photos' })
      const voice = section.getByRole('group', { name: 'Voice notes (speech to text)' })
      await voice.getByLabel('Service').selectOption('custom')
      await voice.getByLabel('Model').fill('whisper-local')
      await voice.getByRole('button', { name: 'Test' }).click()
      await expect(voice.getByText('It works.')).toBeVisible()
      expect(seen.some((s) => s === 'POST /v1/audio/transcriptions')).toBe(true)
      await voice.getByRole('button', { name: 'Save' }).click() // a server on this machine is free: no price asked
      await expect(page.getByTestId('media-status-transcribe')).toHaveText('On. This one is free.')
      await expect(voice.getByText(/Superaide doesn't know this model's price/)).toHaveCount(0)

      const photos = section.getByRole('group', { name: 'Photos and images' })
      await photos.getByLabel('Service').selectOption('custom')
      await photos.getByLabel('Model').fill('llava')
      await photos.getByRole('button', { name: 'Test' }).click()
      await expect(photos.getByText('It works.')).toBeVisible()
      expect(seen.some((s) => s === 'POST /v1/chat/completions')).toBe(true)
      await section.scrollIntoViewIfNeeded()
      await page.screenshot({ path: shot('media-on-1280') })
      // Both tests were counted against the spend cap, at no cost.
      const rows = await query<{ purpose: string; cost_usd: string }>("SELECT purpose, cost_usd FROM spend_ledger WHERE purpose LIKE 'media_%' ORDER BY purpose")
      expect(rows.map((r) => r.purpose)).toEqual(['media_stt', 'media_vision'])
      expect(rows.every((r) => Number(r.cost_usd) === 0)).toBe(true)
    } finally {
      server.close()
    }
  })

  test('a failing server: the test says so in a plain sentence and never shows what the server sent', async ({ page, owner }) => {
    void owner
    const { server, url } = await localServer(true)
    try {
      await page.goto('/dashboard/chat')
      await goToDashboardPage(page, 'AI model')
      const custom = page.getByRole('listitem', { name: 'Other OpenAI-compatible service', exact: true })
      await custom.getByRole('button', { name: 'Connect' }).click()
      await custom.getByLabel('Address (base URL)').fill(url)
      await custom.getByRole('button', { name: 'Save' }).click()
      await expect(custom.getByText('Connected', { exact: true })).toBeVisible()
      const voice = page.getByRole('region', { name: 'Voice notes and photos' }).getByRole('group', { name: 'Voice notes (speech to text)' })
      await voice.getByLabel('Service').selectOption('custom')
      await voice.getByLabel('Model').fill('whisper-local')
      await voice.getByRole('button', { name: 'Test' }).click()
      await expect(voice.getByText(/answered with an error \(500\)/)).toBeVisible()
      await expect(page.getByText('internal secret detail')).toHaveCount(0)
      expect(await query('SELECT role FROM model_role')).toEqual([]) // a test saves nothing
    } finally {
      server.close()
    }
  })
  // The e2e API has no Claude key, so the server's view is replaced with what it sends when Claude is connected and the
  // owner never chose a photo service (the backend side is covered by tests/brain/test_media.py). The Turn off call is real.
  test('photos default to Claude: the page and Check my setup say where photos go, and one click turns it off for good', async ({ page, owner }) => {
    void owner
    let off = false
    const real = async (route: import('@playwright/test').Route) => route.fetch()
    await page.route('**/api/app/media-providers', async (route) => {
      if (route.request().method() !== 'GET' || off) return route.continue()
      const body = await (await real(route)).json()
      const vision = body.capabilities.find((c: { id: string }) => c.id === 'vision')
      Object.assign(vision, {
        choice: { provider: 'anthropic', model: 'claude-haiku-5-5' },
        on: true,
        default: true,
        providers: [{ key: 'anthropic', label: 'Claude (Anthropic)', models: ['claude-haiku-5-5'], ready: true }],
        vendor_notice:
          'Photos people send are read by Claude (Anthropic) using claude-haiku-5-5, which means each photo is sent to that company. This is on because Claude is connected. Turn it off to stop.',
      })
      await route.fulfill({ json: body })
    })
    await page.route('**/api/app/setup-check', async (route) => {
      const body = await (await real(route)).json()
      if (!off) {
        body.results = body.results.filter((r: { name: string }) => r.name !== 'Photo reading')
        body.results.push({
          name: 'Photo reading',
          status: 'ok',
          cause: 'Photos people send are read by Claude (claude-haiku-5-5), which means the photo is sent to Anthropic. Nobody chose this: it is on because Claude is connected. Voice notes stay off.',
          fix: 'Turn it off: AI model page, Voice notes and photos, Turn off.',
        })
      }
      await route.fulfill({ json: body })
    })
    await page.setViewportSize({ width: 1280, height: 900 })
    const section = await openSection(page)
    await expect(page.getByTestId('media-notice-vision')).toContainText('sent to that company')
    await expect(page.getByTestId('media-status-transcribe')).toHaveText('Off. Nothing is sent.')
    await section.scrollIntoViewIfNeeded()
    await page.screenshot({ path: shot('media-default-1280') })
    await page.goto('/dashboard/connections')
    await page.getByTestId('setup-check').getByRole('button', { name: 'Check my setup' }).click()
    const row = page.getByTestId('setup-result').filter({ hasText: 'Photo reading' })
    await expect(row).toContainText('sent to Anthropic')
    await row.scrollIntoViewIfNeeded()
    await page.screenshot({ path: shot('setup-check-photos') })
    off = true
    await row.getByRole('button', { name: 'Turn off photo reading' }).click()
    await expect(page.getByTestId('setup-result').filter({ hasText: 'Photo reading' }).getByRole('button')).toHaveCount(0)
    expect(await query('SELECT role, provider FROM model_role')).toEqual([{ role: 'vision', provider: 'off' }])
    const after = await openSection(page)
    await expect(page.getByTestId('media-notice-vision')).toHaveCount(0)
    await expect(page.getByTestId('media-status-vision')).toHaveText('Off. Nothing is sent.')
    await after.scrollIntoViewIfNeeded()
    await page.screenshot({ path: shot('media-default-off-1280') })
  })
  test('a built-in voice price is labelled as a list price the owner can change', async ({ page, owner }) => {
    void owner
    await page.route('**/api/app/media-providers', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const body = await (await route.fetch()).json()
      const voice = body.capabilities.find((c: { id: string }) => c.id === 'transcribe')
      Object.assign(voice, {
        choice: { provider: 'openai', model: 'whisper-1' },
        on: true,
        price: { known: true, free: false, unit: 'minute', usd: 0.006, builtin: true },
        providers: [{ key: 'openai', label: 'OpenAI', models: ['whisper-1'], ready: true }],
      })
      await route.fulfill({ json: body })
    })
    await page.setViewportSize({ width: 1280, height: 900 })
    const section = await openSection(page)
    const label = section.getByTestId('media-list-price')
    await expect(label).toContainText('US$0.006 per audio minute')
    await expect(label).toContainText('not a checked price')
    await section.scrollIntoViewIfNeeded()
    await page.screenshot({ path: shot('media-list-price-1280') })
    await label.getByRole('button', { name: 'Change it' }).click()
    await expect(section.getByLabel('US$ per audio minute')).toHaveValue('0.006')
  })
})
