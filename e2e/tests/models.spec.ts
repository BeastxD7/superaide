// The AI model page (/dashboard/models, ADR 0007): connect a provider, test it, choose models,
// and what's refused. A tiny OpenAI-compatible server on this machine stands in for a local
// model server, so nothing leaves the machine and nothing is spent.
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import type { Page } from '@playwright/test'
import { expect, newPerson, query, test } from '../src/fixtures'
import { auditActions, goToDashboardPage, storedSecrets } from '../src/helpers/workspace'

const KEY = 'local-server-secret-key-0123456789'

function fakeModelServer(): Promise<{ server: Server; url: string; seen: string[] }> {
  const seen: string[] = []
  const server = createServer((req, res) => {
    seen.push(`${req.method} ${req.url} ${req.headers.authorization ?? ''}`)
    if (req.url?.endsWith('/models')) {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ object: 'list', data: [{ id: 'tiny-llm', object: 'model', created: 1, owned_by: 'me' }] }))
      return
    }
    res.writeHead(404).end()
  })
  return new Promise((resolve) =>
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as AddressInfo
      resolve({ server, url: `http://127.0.0.1:${port}/v1`, seen })
    }),
  )
}

async function openModels(page: Page) {
  await page.goto('/dashboard/chat')
  await goToDashboardPage(page, 'AI model')
  await expect(page.getByRole('heading', { level: 1, name: 'AI model' })).toBeVisible()
}

function card(page: Page, name: string) {
  return page.getByRole('listitem', { name, exact: true })
}

test.describe('AI model page', () => {
  test('lists every provider, and says Ava is offline until one is connected', async ({ page, owner }) => {
    void owner
    await openModels(page)
    for (const name of ['Claude (Anthropic)', 'OpenAI', 'Azure OpenAI / AI Foundry', 'OpenRouter', 'xAI Grok', 'Google Gemini', 'Ollama (on this computer)']) {
      await expect(card(page, name)).toBeVisible()
    }
    await expect(page.getByText('No AI model is connected yet')).toBeVisible()
    await expect(card(page, 'Ollama (on this computer)').getByText(/Local models are free and private/)).toBeVisible()
    await expect(page.getByRole('region', { name: 'Models' })).toHaveCount(0)
  })

  test('connect a local server: test, save, pick a model; the key is never shown again', async ({ page, owner }) => {
    void owner
    const { server, url, seen } = await fakeModelServer()
    try {
      await openModels(page)
      const custom = card(page, 'Other OpenAI-compatible service')
      await custom.getByRole('button', { name: 'Connect' }).click()
      await custom.getByLabel(/API key/).fill(KEY)
      await custom.getByLabel('Address (base URL)').fill(url)
      await custom.getByRole('button', { name: 'Test connection' }).click()
      await expect(custom.getByText('1 models available')).toBeVisible()
      expect(seen.some((s) => s.includes(`Bearer ${KEY}`))).toBe(true)
      expect(await query('SELECT preset FROM llm_provider')).toEqual([]) // testing saves nothing

      await custom.getByRole('button', { name: 'Save' }).click()
      await expect(custom.getByText('Connected', { exact: true })).toBeVisible()
      await expect(page.getByText(KEY)).toHaveCount(0)
      const secrets = await storedSecrets()
      const saved = secrets.find((s) => s.provider === 'llm:custom')
      expect(saved && saved.hint).toBeTruthy()
      expect(saved?.hint).not.toContain(KEY)

      const chat = page.getByRole('group', { name: 'Conversation' })
      await chat.getByLabel('Provider').selectOption('custom')
      await expect(chat.getByLabel('Model')).toHaveValue('tiny-llm')
      for (const role of ['Quick reading', 'Deep work']) {
        await page.getByRole('group', { name: role }).getByLabel('Provider').selectOption('custom')
      }
      // A server on this machine is free, so no price is asked for.
      await expect(page.getByText("Superaide doesn't know this model's price")).toHaveCount(0)
      await page.getByRole('button', { name: 'Save models' }).click()
      await expect(page.getByText('Saved.')).toBeVisible()
      // (The e2e API runs with FAKE_LLM=true, which keeps Ava offline whatever is chosen, so the
      // status line isn't checked here; backend tests cover brain_status for each provider.)
      expect(await query('SELECT role, provider, model FROM model_role ORDER BY role')).toEqual([
        { role: 'chat', provider: 'custom', model: 'tiny-llm' },
        { role: 'deep', provider: 'custom', model: 'tiny-llm' },
        { role: 'triage', provider: 'custom', model: 'tiny-llm' },
      ])
      const actions = await auditActions()
      expect(actions).toContain('provider_saved')
      expect(actions).toContain('role_changed')

      // Disconnecting deletes the key and puts every role back on Claude.
      await custom.getByRole('button', { name: 'Change' }).click()
      await custom.getByRole('button', { name: 'Disconnect' }).click()
      await expect(custom.getByText('Connected', { exact: true })).toHaveCount(0)
      expect((await storedSecrets()).some((s) => s.provider === 'llm:custom')).toBe(false)
      expect(await query('SELECT role FROM model_role')).toEqual([])
    } finally {
      server.close()
    }
  })

  test('addresses that could reach the server itself are refused', async ({ page, owner }) => {
    void owner
    await openModels(page)
    const custom = card(page, 'Other OpenAI-compatible service')
    await custom.getByRole('button', { name: 'Connect' }).click()
    await custom.getByLabel('Address (base URL)').fill('http://169.254.169.254/latest')
    await custom.getByRole('button', { name: 'Save' }).click()
    await expect(custom.getByText("That address isn't allowed.")).toBeVisible()
    await custom.getByLabel('Address (base URL)').fill('https://user:pw@example.com/v1')
    await custom.getByRole('button', { name: 'Test connection' }).click()
    await expect(custom.getByText('Put the key in the key field, not in the address.')).toBeVisible()
    expect(await query('SELECT preset FROM llm_provider')).toEqual([])
  })

  test('a hosted service needs a key, and a server that is not running says so plainly', async ({ page, owner }) => {
    void owner
    await openModels(page)
    const openrouter = card(page, 'OpenRouter')
    await openrouter.getByRole('button', { name: 'Connect' }).click()
    await openrouter.getByRole('button', { name: 'Save' }).click()
    await expect(openrouter.getByText('OpenRouter needs an API key.')).toBeVisible()

    const ollama = card(page, 'Ollama (on this computer)')
    await ollama.getByRole('button', { name: 'Connect' }).click()
    await ollama.getByLabel('Address (base URL)').fill('http://127.0.0.1:9/v1') // nothing listens on port 9
    await ollama.getByRole('button', { name: 'Test connection' }).click()
    await expect(ollama.getByText(/Couldn't reach Ollama/)).toBeVisible()
  })

  test('web search: explains the cost, needs a real key, and stores nothing on a bad one', async ({ page, owner }) => {
    void owner
    await openModels(page)
    const section = page.getByRole('region', { name: 'Web search' })
    await expect(section.getByText(/Each search counts about US\$0\.010/)).toBeVisible()
    await section.getByRole('button', { name: 'Add a key' }).click()
    await expect(section.getByRole('button', { name: 'Save' })).toBeDisabled()
    await section.getByLabel('Brave Search API key').fill('short')
    await section.getByRole('button', { name: 'Save' }).click()
    await expect(section.getByText(/at least 10 characters/)).toBeVisible()
    expect((await storedSecrets()).some((s) => s.provider === 'brave')).toBe(false)
  })

  test('someone who has not hired Ava cannot read or change the model settings', async ({ api }) => {
    await api.signup(newPerson('Not Hired'))
    expect((await api.get('/app/providers')).status()).toBe(403)
    expect((await api.post('/app/providers/openrouter', { api_key: KEY })).status()).toBe(403)
  })
})
