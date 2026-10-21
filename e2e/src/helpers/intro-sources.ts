// Helpers for the introduction's three sources: files, links and the video call.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, type Locator, type Page } from '@playwright/test'
import { query, type Api } from '../fixtures'

export type UploadFile = { name: string; mimeType: string; buffer: Buffer }

// The call's questions, as the API asks them (backend/src/superaide/app/onboarding.py).
export const CALL_QUESTIONS = [
  'Tell me a little about yourself and what you do.',
  'What does a normal week look like for you? When are you hardest to reach?',
  'Who are the people I\'ll hear from most, and how do you know them?',
  'Is there anything I should always be careful with, or never share?',
]
export const CLOSING_LINE = "Thanks, that's really helpful. I've saved everything to my notebook."

// --- getting to the "About you" step ------------------------------------------------

// From a fresh hire: answer "hello" through the API, then open the introduction.
export async function openIntroduction(page: Page, api: Api): Promise<void> {
  await api.ok(api.get('/app/onboarding'))
  await api.answer('hello', { choice: 'Nice to meet you' })
  await page.goto('/meet')
  await expect(introTabs(page)).toBeVisible()
}

export function introTabs(page: Page): Locator {
  return page.getByRole('tablist', { name: 'How to introduce yourself' })
}

export async function openTab(page: Page, name: 'Tell me here' | 'Files & links' | 'Video call'): Promise<void> {
  const tab = page.getByRole('tab', { name })
  await tab.click()
  await expect(tab).toHaveAttribute('aria-selected', 'true')
}

// --- files ----------------------------------------------------------------------------

export function textFile(name: string, text: string, mimeType = 'text/plain'): UploadFile {
  return { name, mimeType, buffer: Buffer.from(text, 'utf8') }
}

// Pick a file the way a person does: the "choose a file" button opens the file chooser.
export async function chooseFile(page: Page, file: UploadFile): Promise<void> {
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'choose a file' }).click()
  await (await chooser).setFiles(file)
}

export function fileRow(page: Page, title: string): Locator {
  return page.getByRole('listitem').filter({ has: page.getByRole('button', { name: `Remove ${title}`, exact: true }) })
}

// A minimal, valid one-page PDF. With no lines it has a drawn box and no text at all,
// like a scanned page.
export function pdf(lines: string[]): UploadFile & { name: string } {
  const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)')
  const content = lines.length
    ? `BT /F1 12 Tf 72 720 Td ${lines.map((l, i) => `${i ? '0 -16 Td ' : ''}(${esc(l)}) Tj`).join(' ')} ET`
    : '0 0 1 rg 72 600 200 100 re f'
  const resources = lines.length ? '/Resources << /Font << /F1 4 0 R >> >> ' : ''
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] ${resources}/Contents 5 0 R >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(content, 'latin1')} >>\nstream\n${content}\nendstream`,
  ]
  let out = '%PDF-1.4\n'
  const offsets: number[] = []
  objects.forEach((body, i) => {
    offsets.push(Buffer.byteLength(out, 'latin1'))
    out += `${i + 1} 0 obj\n${body}\nendobj\n`
  })
  const xref = Buffer.byteLength(out, 'latin1')
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  out += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return { name: 'resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from(out, 'latin1') }
}

// A tiny Word document (made with python-docx's reader in mind; see fixtures/intro-sources).
export function docx(): UploadFile {
  const path = fileURLToPath(new URL('../../fixtures/intro-sources/resume.docx', import.meta.url))
  return {
    name: 'resume.docx',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: readFileSync(path),
  }
}

// --- what was stored ------------------------------------------------------------------

export type DocRow = { id: string; title: string; source: string; kind: string; status: string; removed: boolean; text: string }

export function documents(): Promise<DocRow[]> {
  return query<DocRow>(
    'SELECT id::text, title, source, kind, status, removed_at IS NOT NULL AS removed, text FROM document ORDER BY created_at',
  )
}

export type FactRow = { content: string; source: string; document_id: string | null; active: boolean }

export function facts(): Promise<FactRow[]> {
  return query<FactRow>(
    'SELECT content, source, document_id::text, valid_to IS NULL AS active FROM memory_item ORDER BY created_at',
  )
}

export async function agentLines(): Promise<string[]> {
  const rows = await query<{ content: string }>("SELECT content FROM event WHERE direction = 'out' ORDER BY created_at")
  return rows.map((r) => r.content)
}

// As if the model had read a document: one fact from it, and the document marked read.
export async function pretendRead(documentId: string, fact: string): Promise<void> {
  await query(
    `INSERT INTO memory_item (id, tenant_id, agent_id, created_at, content, subject_person_ids, source_event_ids,
       allowed_kind, allowed_person_ids, min_trust, valid_from, category, source, document_id, confidence)
     SELECT gen_random_uuid(), d.tenant_id, d.agent_id, now(), $2, ARRAY[d.owner_person_id], '{}',
       'persons', ARRAY[d.owner_person_id], 2, now(), 'work', 'document', d.id, 1
     FROM document d WHERE d.id = $1::uuid`,
    [documentId, fact],
  )
  await query("UPDATE document SET status = 'read' WHERE id = $1::uuid", [documentId])
}

// --- the video call ---------------------------------------------------------------------

export function callDialog(page: Page): Locator {
  return page.getByRole('dialog', { name: 'Video call with Ava' })
}

// Headless Chromium has no voice, so every line Ava says waits out its reading time.
// This stands in a voice that speaks each line in a moment, to keep the call quick.
export async function quickVoice(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const synth = window.speechSynthesis
    if (!synth) return
    const add = synth.addEventListener.bind(synth)
    synth.addEventListener = ((type: string, fn: EventListenerOrEventListenerObject, opts?: AddEventListenerOptions) => {
      add(type, fn, opts)
      if (type === 'voiceschanged') {
        window.setTimeout(() => (typeof fn === 'function' ? fn(new Event(type)) : fn.handleEvent(new Event(type))), 0)
      }
    }) as typeof synth.addEventListener
    synth.speak = (u: SpeechSynthesisUtterance) => {
      window.setTimeout(() => {
        u.onstart?.call(u, new Event('start') as SpeechSynthesisEvent)
        window.setTimeout(() => u.onend?.call(u, new Event('end') as SpeechSynthesisEvent), 40)
      }, 10)
    }
  })
}

// A stand-in for the browser's speech recognition: the test "says" words with sayAloud().
export async function fakeRecognition(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type Handler = ((e: unknown) => void) | null
    class FakeRecognition {
      lang = ''
      continuous = false
      interimResults = false
      maxAlternatives = 1
      onstart: Handler = null
      onresult: Handler = null
      onerror: Handler = null
      onend: Handler = null
      results: { isFinal: boolean; 0: { transcript: string; confidence: number }; length: number }[] = []
      start() {
        ;(window as unknown as { __recognizer: FakeRecognition }).__recognizer = this
        window.setTimeout(() => this.onstart?.(new Event('start')), 10)
      }
      stop() {
        window.setTimeout(() => this.onend?.(new Event('end')), 10)
      }
      abort() {
        window.setTimeout(() => this.onend?.(new Event('end')), 10)
      }
      hear(text: string, final: boolean) {
        this.results = this.results.filter((r) => r.isFinal)
        this.results.push({ isFinal: final, 0: { transcript: text, confidence: 0.9 }, length: 1 })
        this.onresult?.({ resultIndex: this.results.length - 1, results: this.results })
      }
    }
    const w = window as unknown as Record<string, unknown>
    w.SpeechRecognition = FakeRecognition
    w.webkitSpeechRecognition = FakeRecognition
  })
}

export async function sayAloud(page: Page, text: string, final = true): Promise<void> {
  await page.evaluate(
    ([t, f]) => {
      const rec = (window as unknown as { __recognizer?: { hear: (t: string, f: boolean) => void } }).__recognizer
      if (!rec) throw new Error('nobody is listening')
      rec.hear(t, f)
    },
    [text, final] as const,
  )
}

// The camera fails the way a browser reports it (NotAllowedError when blocked, NotFoundError with no camera).
export async function cameraFails(page: Page, name: 'NotAllowedError' | 'NotFoundError'): Promise<void> {
  await page.addInitScript((errorName) => {
    if (!navigator.mediaDevices) return
    navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Camera unavailable', errorName))
  }, name)
}

export async function startCall(page: Page): Promise<Locator> {
  await openTab(page, 'Video call')
  await page.getByRole('button', { name: 'Start the call' }).click()
  const dialog = callDialog(page)
  await expect(dialog).toBeVisible()
  return dialog
}

// Answer the question on screen by typing. Headless Chromium can't turn speech into
// text, so switch to typing if the call is still waiting for the microphone.
export async function typeAnswer(dialog: Locator, question: string, answer: string, how: 'enter' | 'click' = 'enter'): Promise<void> {
  await expect(dialog.getByText(question, { exact: true })).toBeVisible({ timeout: 20_000 })
  const box = dialog.getByRole('textbox', { name: 'Type your answer' })
  if (!(await box.isVisible())) await dialog.getByRole('button', { name: 'Type instead' }).click()
  await expect(box).toBeEditable()
  await box.fill(answer)
  const send = dialog.getByRole('button', { name: 'Send', exact: true })
  await expect(send).toBeEnabled({ timeout: 20_000 })
  if (how === 'enter') await box.press('Enter')
  else await send.click()
}

export async function leaveCall(page: Page, dialog: Locator): Promise<void> {
  await dialog.getByRole('button', { name: 'Leave the call' }).click()
  await dialog.getByRole('group', { name: 'Confirm leaving the call' }).getByRole('button', { name: 'Leave', exact: true }).click()
  await expect(dialog).toBeHidden()
}

// --- keyboard ---------------------------------------------------------------------------

// Press Tab, as a person would, until `target` has focus (it must be reachable in a few presses).
export async function tabTo(page: Page, target: Locator, maxPresses = 6): Promise<void> {
  await expect(target).toBeVisible()
  for (let i = 0; i < maxPresses; i++) {
    await page.keyboard.press('Tab')
    if (await target.evaluate((el) => el === document.activeElement)) return
  }
  await expect(target, `reachable with Tab within ${maxPresses} presses`).toBeFocused({ timeout: 1 })
}

// --- layout -----------------------------------------------------------------------------

export async function hasNoSidewaysScroll(page: Page): Promise<void> {
  const { scroll, width } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    width: window.innerWidth,
  }))
  expect(scroll, 'the page should not scroll sideways').toBeLessThanOrEqual(width)
}

export async function insideViewport(page: Page, locator: Locator, what: string): Promise<void> {
  const box = await locator.boundingBox()
  const viewport = page.viewportSize()
  expect(box, `${what} should be on screen`).not.toBeNull()
  expect(viewport).not.toBeNull()
  if (!box || !viewport) return
  expect(box.x, `${what} starts on screen`).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width, `${what} ends within the screen width`).toBeLessThanOrEqual(viewport.width + 0.5)
}
