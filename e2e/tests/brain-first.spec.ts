// Issue #13: the introduction starts by setting up Ava's brain, then what the owner shares is read live.
// Muted by default: needs the fake Anthropic and a hosted API with credits on (E2E_BRAIN=1, slot 72):
//   E2E_BRAIN=1 E2E_SLOT=72 npx playwright test tests/brain-first.spec.ts --project=desktop
import { FAKE_ANTHROPIC_URL } from '../src/env'
import { expect, query, test } from '../src/fixtures'
import { chooseFile, fileRow, openTab, textFile } from '../src/helpers/intro-sources'

test.skip(process.env.E2E_BRAIN !== '1', 'needs E2E_BRAIN=1 (a fake Anthropic and credits switched on)')
test.use({ ownerState: 'hired' })

const RESUME = 'I lead applied AI at Acme in Singapore.\nMost of my week is client work.'
const brainGroup = (page: import('@playwright/test').Page) => page.getByRole('group', { name: /Choose .*'s brain/ })

async function modelCalls(): Promise<number> {
  return (await (await fetch(`${FAKE_ANTHROPIC_URL}/count`)).json()).calls as number
}

test.describe('brain first', () => {
  test('credits in one click, then a resume is read live and the facts are saved', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    await expect(page.getByText(/Step 1 of 9: Brain/)).toBeVisible()
    const group = brainGroup(page)
    await expect(group.getByText(/up to US\$0\.50 a day/)).toBeVisible()
    const before = await modelCalls()
    await group.getByRole('button', { name: 'Use Superaide AI credits' }).click()

    await expect(page.getByText(/Step 2 of 9: Hello/)).toBeVisible()
    await page.getByRole('button', { name: 'Nice to meet you' }).click()
    await openTab(page, 'Files & links')
    await chooseFile(page, textFile('resume.txt', RESUME))

    await expect(page.getByText('Read resume.txt: 1 fact saved.')).toBeVisible()
    await expect(fileRow(page, 'resume.txt')).toContainText('1 facts')
    expect(await modelCalls()).toBeGreaterThan(before) // the model really read it
    expect(await query("SELECT key_source, provider FROM spend_ledger WHERE status = 'settled' LIMIT 1")).toEqual([
      { key_source: 'platform', provider: 'anthropic' },
    ])
    const facts = await query<{ content: string }>("SELECT content FROM memory_item WHERE source = 'document'")
    expect(facts.map((f) => f.content)).toContain('The owner leads applied AI at Acme in Singapore.')
    expect(await query('SELECT key_source FROM agent_instance')).toEqual([{ key_source: 'platform' }])
  })

  test('skip: what is shared is saved, then read automatically once credits are switched on', async ({ page, api, owner }) => {
    void owner
    await page.goto('/meet')
    await brainGroup(page).getByRole('button', { name: /^Skip for now/ }).click()
    await expect(page.getByText(/Step 2 of 9: Hello/)).toBeVisible()
    await page.getByRole('button', { name: 'Nice to meet you' }).click()
    await openTab(page, 'Files & links')
    await chooseFile(page, textFile('resume.txt', RESUME))
    await expect(page.getByText(/resume\.txt is saved\. It will be read as soon as Ava's brain is set up\./)).toBeVisible()
    await expect(fileRow(page, 'resume.txt')).toContainText('Saved, not read yet')
    expect(await query('SELECT count(*)::int AS n FROM memory_item WHERE source = \'document\'')).toEqual([{ n: 0 }])

    // Later: switch credits on (what the AI model page's button does; the dashboard opens once the
    // introduction is finished). The waiting document is read in the background.
    await api.ok(api.post('/app/providers/credits', { on: true }))
    await expect
      .poll(async () => (await query<{ n: number }>("SELECT count(*)::int AS n FROM memory_item WHERE source = 'document'"))[0].n, {
        timeout: 60_000,
        intervals: [1_000, 2_000, 5_000],
      })
      .toBe(1)
    expect(await query("SELECT status FROM document WHERE title = 'resume.txt'")).toEqual([{ status: 'read' }])
  })

  test('own key path: a bad key is refused and nothing advances', async ({ page, owner }) => {
    void owner
    await page.goto('/meet')
    const group = brainGroup(page)
    await group.getByRole('button', { name: 'Connect my own key' }).click()
    await group.getByLabel('Claude API key').fill('not-a-claude-key')
    await group.getByRole('button', { name: 'Save key' }).click()
    await expect(group.getByText(/doesn't look like a Claude API key/)).toBeVisible()
    await expect(page.getByText(/Step 1 of 9: Brain/)).toBeVisible()
    expect(await query("SELECT key_source FROM agent_instance")).toEqual([{ key_source: 'owner' }])
  })
})
