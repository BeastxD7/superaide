// The three ways to introduce yourself to Ava: files, links and the video call.
//
// Every test starts just after hiring Ava, at the "About you" step. Files are read
// in offline mode (the stack runs a fake model), so they are "saved, not read yet"
// and produce no facts of their own; pretendRead() stands in for the model where a
// test needs a fact that came from a file.
import { randomUUID } from 'node:crypto'
import { expect, newPerson, test } from '../src/fixtures'
import {
  CALL_QUESTIONS,
  agentLines,
  callDialog,
  cameraFails,
  chooseFile,
  docx,
  documents,
  facts,
  fakeRecognition,
  fileRow,
  introTabs,
  leaveCall,
  openIntroduction,
  openTab,
  pdf,
  pretendRead,
  quickVoice,
  sayAloud,
  startCall,
  tabTo,
  textFile,
  typeAnswer,
  type UploadFile,
} from '../src/helpers/intro-sources'

test.use({ ownerState: 'hired' })

const CSRF = { 'X-Requested-With': 'superaide-e2e' }
const UNSUPPORTED = "That file type isn't supported. Upload a PDF, Word (.docx), .txt or .md file."
const SAVED = 'Saved, not read yet'

// --- Files ------------------------------------------------------------------------------

test.describe('files', () => {
  test.beforeEach(async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
    await openTab(page, 'Files & links')
  })

  test('a .txt and a .md file each appear in the list, saved and waiting to be read', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()

    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad in Singapore.\nMost of my week is client work.'))
    await expect(fileRow(page, 'about-me.txt')).toContainText(SAVED)
    await expect(page.getByText("I've saved about-me.txt. I'll read it properly once an AI model is connected.")).toBeVisible()

    await chooseFile(page, textFile('notes.md', '# Notes\n\n- Prefers mornings for deep work\n- Travels to Bengaluru monthly', 'text/markdown'))
    await expect(fileRow(page, 'notes.md')).toContainText(SAVED)
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()

    const docs = await documents()
    expect(docs.map((d) => [d.title, d.kind, d.status, d.removed])).toEqual([
      ['about-me.txt', 'text', 'stored', false],
      ['notes.md', 'text', 'stored', false],
    ])
    expect(docs[0].text).toContain('applied AI at PebbleRoad')
    expect(docs[1].text).toContain('Travels to Bengaluru monthly')
  })

  test('a text PDF and a Word document are accepted, their text kept, and listed', async ({ page }) => {
    await chooseFile(page, pdf(['Ananya Menon (applied AI)', 'Leads applied AI at PebbleRoad in Singapore.']))
    await expect(fileRow(page, 'resume.pdf')).toContainText(SAVED)

    await chooseFile(page, docx())
    await expect(fileRow(page, 'resume.docx')).toContainText(SAVED)

    const docs = await documents()
    expect(docs.map((d) => [d.title, d.kind])).toEqual([
      ['resume.pdf', 'pdf'],
      ['resume.docx', 'docx'],
    ])
    expect(docs[0].text).toContain('Leads applied AI at PebbleRoad in Singapore.')
    expect(docs[1].text).toContain('Builds document intelligence products.')
  })

  test('a file dropped on the drop zone is shared too', async ({ page }) => {
    const dropped = await page.evaluateHandle(() => {
      const data = new DataTransfer()
      data.items.add(new File(['I lead applied AI at PebbleRoad in Singapore.'], 'dropped.md', { type: 'text/markdown' }))
      return data
    })
    await page.getByText('Drop your resume or CV here').dispatchEvent('drop', { dataTransfer: dropped })
    await expect(fileRow(page, 'dropped.md')).toContainText(SAVED)
    expect((await documents()).map((d) => d.title)).toEqual(['dropped.md'])
  })

  test('removing a file takes it off the list and its facts out of the notebook', async ({ page, api }) => {
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    const [doc] = await documents()
    await pretendRead(doc.id, 'Ananya leads applied AI at PebbleRoad.')
    const before = await api.ok<{ memories: { text: string }[] }>(api.get('/app/notebook'))
    expect(before.memories.map((m) => m.text)).toContain('Ananya leads applied AI at PebbleRoad.')

    await page.getByRole('button', { name: 'Remove about-me.txt' }).click()
    await expect(fileRow(page, 'about-me.txt')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()

    expect((await documents()).map((d) => d.removed)).toEqual([true])
    expect((await facts()).filter((f) => f.document_id === doc.id).map((f) => f.active)).toEqual([false])
    const after = await api.ok<{ memories: { text: string }[] }>(api.get('/app/notebook'))
    expect(after.memories.map((m) => m.text)).not.toContain('Ananya leads applied AI at PebbleRoad.')
    // …and it stays gone after a reload.
    await page.reload()
    await expect(introTabs(page)).toBeVisible()
    await openTab(page, 'Files & links')
    await expect(fileRow(page, 'about-me.txt')).toHaveCount(0)
  })

  test('a file that has been read shows how many facts came from it, in plain English', async ({ page }) => {
    test.fail(true, 'BUG: the file list says "1 facts" for a file with one fact')
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    const [doc] = await documents()
    await pretendRead(doc.id, 'Ananya leads applied AI at PebbleRoad.')
    await page.reload()
    const row = fileRow(page, 'about-me.txt')
    await expect(row).toContainText(/\d+ facts?/)
    await expect(row).toContainText(/\b1 fact\b/, { timeout: 3_000 })
  })

  test('coming back later: files are still there, and a removed file can be shared again', async ({ page }) => {
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()

    // A reload opens straight on Files & links, because something is already shared.
    await page.reload()
    await expect(page.getByRole('tab', { name: 'Files & links' })).toHaveAttribute('aria-selected', 'true')
    await expect(fileRow(page, 'about-me.txt')).toContainText(SAVED)

    await page.getByRole('button', { name: 'Remove about-me.txt' }).click()
    await expect(fileRow(page, 'about-me.txt')).toHaveCount(0)
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    expect((await documents()).filter((d) => !d.removed).map((d) => d.title)).toEqual(['about-me.txt'])
  })

  test('sharing the same file twice says so and keeps one copy', async ({ page }) => {
    const file = textFile('about-me.txt', 'I lead applied AI at PebbleRoad.')
    await chooseFile(page, file)
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    const lines = (await agentLines()).length

    await chooseFile(page, file)
    await expect(page.getByText("You've already shared this.")).toBeVisible()
    await expect(fileRow(page, 'about-me.txt')).toHaveCount(1)
    expect(await documents()).toHaveLength(1)
    expect(await agentLines()).toHaveLength(lines)
  })

  test('Continue moves on to "Your people" once a file is shared', async ({ page }) => {
    await chooseFile(page, docx())
    await expect(fileRow(page, 'resume.docx')).toBeVisible()
    await page.getByRole('button', { name: 'Continue' }).click()
    await expect(page.getByText('Step 3 of 8: Your people')).toBeVisible()
    await expect(page.getByText("Here's where I am: 0 things about you in my notebook from 1 document.")).toBeVisible()
    await expect(page.getByRole('button', { name: 'These are my people' })).toBeVisible()
  })

  // Refused files: a clear message, nothing stored, nothing said. Each group tries its
  // files one after another on the same page, then shows a good file still goes through.
  const PNG = Buffer.from('89504e470d0a1a0a0000000d4948445200000001000000010806000000', 'hex')
  type Refusal = { what: string; file: UploadFile; message: string }
  const refusalGroups: Record<string, Refusal[]> = {
    'files of a type it cannot read': [
      { what: 'an image (.png)', file: { name: 'photo.png', mimeType: 'image/png', buffer: PNG }, message: UNSUPPORTED },
      {
        what: 'a program (.exe)',
        file: { name: 'setup.exe', mimeType: 'application/x-msdownload', buffer: Buffer.from('MZ\x90\x00\x03\x00\x00\x00', 'latin1') },
        message: UNSUPPORTED,
      },
      {
        what: 'an old Word file (.doc)',
        file: { name: 'cv.doc', mimeType: 'application/msword', buffer: Buffer.from('d0cf11e0a1b11ae1', 'hex') },
        message: "If it's an older .doc file, open it in Word and save it as .docx first.",
      },
    ],
    'empty, blank and over-size files': [
      { what: 'an empty file', file: textFile('empty.txt', ''), message: 'That file is empty.' },
      { what: 'a file of blank lines', file: textFile('blank.txt', '\n \n\t\n'), message: 'No text was found in this file.' },
      {
        what: 'a file over 10 MB',
        file: { name: 'huge.txt', mimeType: 'text/plain', buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 'a') },
        message: 'That file is too big. Files can be up to 10 MB.',
      },
    ],
    'files whose contents do not match their name, and a PDF with no text': [
      {
        what: 'a .pdf that is not really a PDF',
        file: { name: 'resume.pdf', mimeType: 'application/pdf', buffer: Buffer.from('just some text pretending') },
        message: "This file ends in .pdf but isn't a real PDF. Try exporting it as a PDF again.",
      },
      {
        what: 'a .docx that is not really a Word document',
        file: { ...docx(), buffer: Buffer.from('not a zip at all') },
        message: "This file ends in .docx but isn't a real Word document. Try saving it as .docx again.",
      },
      {
        what: 'a .txt that is really an image',
        file: { name: 'notes.txt', mimeType: 'text/plain', buffer: PNG },
        message: "This file doesn't look like plain text. If it's a PDF or Word file, upload it with its real extension.",
      },
      {
        what: 'a scanned PDF with no text',
        file: { ...pdf([]), name: 'scan.pdf' },
        message: 'This PDF looks scanned; no text found. Upload a text-based PDF or a Word file, or paste the text.',
      },
    ],
  }
  for (const [group, refusals] of Object.entries(refusalGroups)) {
    test(`refuses ${group} with a clear message and stores nothing`, async ({ page }) => {
      const lines = (await agentLines()).length
      for (const { what, file, message } of refusals) {
        await test.step(what, async () => {
          const response = page.waitForResponse('**/api/app/intro/files')
          await chooseFile(page, file)
          expect((await response).status()).toBe(422)
          await expect(page.getByText(message)).toBeVisible()
          await expect(page.getByRole('button', { name: `Remove ${file.name}` })).toHaveCount(0)
          await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
          expect(await documents()).toEqual([])
          expect(await facts()).toEqual([])
          expect(await agentLines()).toHaveLength(lines)
        })
      }
      // The page still works: a good file goes through afterwards and clears the message.
      await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
      await expect(fileRow(page, 'about-me.txt')).toBeVisible()
      await expect(page.getByText(refusals[refusals.length - 1].message)).toHaveCount(0)
    })
  }

  test('a file name with path characters is cut down to the name alone', async ({ page, api }) => {
    void api
    await chooseFile(page, textFile('../../etc/passwd.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'passwd.txt')).toBeVisible()

    // Straight at the API, too, with a Windows-style path.
    const r = await page.request.post('/api/app/intro/files', {
      headers: CSRF,
      multipart: { file: { name: 'C:\\Users\\ananya\\..\\cv.txt', mimeType: 'text/plain', buffer: Buffer.from('Another note about me.') } },
    })
    expect(r.status(), await r.text()).toBe(200)

    const docs = await documents()
    expect(docs.map((d) => [d.title, d.source])).toEqual([
      ['passwd.txt', 'passwd.txt'],
      ['cv.txt', 'cv.txt'],
    ])
    for (const d of docs) expect(d.title).not.toMatch(/[\\/]|\.\./)
  })

  test('a hostile file name is shown as plain text, never run', async ({ page }) => {
    let alerted = false
    page.on('dialog', (d) => {
      alerted = true
      void d.dismiss()
    })
    const name = '<img src=x onerror=alert(1)>.txt'
    await chooseFile(page, textFile(name, 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, name)).toBeVisible()
    await expect(fileRow(page, name)).toContainText(name)
    expect(alerted).toBe(false)
  })

  test('while a file is being read, it says so and Continue waits', async ({ page }) => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    await page.route('**/api/app/intro/files', async (route) => {
      await gate
      await route.continue()
    })
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(page.getByText('Reading about-me.txt…')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Add' })).toBeDisabled()
    release()
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    await expect(page.getByText('Reading about-me.txt…')).toHaveCount(0)
  })

  test('when the app cannot reach the server, uploading says so and nothing is stored', async ({ page }) => {
    await page.route('**/api/app/intro/files', (route) => route.abort('connectionrefused'))
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(page.getByText(/Can't reach Superaide/)).toBeVisible()
    expect(await documents()).toEqual([])
  })

  test('a server error while uploading shows a message a person can act on', async ({ page }) => {
    test.fail(true, 'BUG: a 500 from the server shows "Request failed with status code 500"')
    await page.route('**/api/app/intro/files', (route) =>
      route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' }),
    )
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(page.getByText('Reading about-me.txt…')).toHaveCount(0)
    await expect(page.getByText(/status code/)).toHaveCount(0, { timeout: 3_000 })
    await expect(page.getByText(/went wrong|try again/i)).toBeVisible({ timeout: 3_000 })
    expect(await documents()).toEqual([])
  })

  test('a refused file is announced to screen readers', async ({ page }) => {
    test.fail(true, 'BUG: file and link errors are plain divs, not role=alert or a live region')
    await chooseFile(page, { name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from('png') })
    await expect(page.getByText(UNSUPPORTED)).toBeVisible()
    // (Next.js's own route announcer is an empty role=alert, so look for one holding the message.)
    await expect(page.getByRole('alert').filter({ hasText: UNSUPPORTED })).toBeVisible({ timeout: 3_000 })
  })

  test('a warning about a partly readable file stays on screen', async ({ page }) => {
    const response = page.waitForResponse('**/api/app/intro/files')
    await chooseFile(page, { ...pdf(['Hi']), name: 'short.pdf' })
    // The server does say so…
    const body = (await (await response).json()) as { warnings: string[] }
    expect(body.warnings.join(' ')).toMatch(/only a little text could be read/)
    await expect(fileRow(page, 'short.pdf')).toBeVisible()
    // …but once Ava's reply appears, the page no longer does.
    await expect(page.getByText(/only a little text could be read/)).toBeVisible({ timeout: 3_000 })
  })

  test('removing a file that was already removed elsewhere leaves a sane list', async ({ page, api }) => {
    test.fail(true, 'BUG: removing a file already removed in another tab fails silently (unhandled 404) and the row stays')
    await chooseFile(page, textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()
    const [doc] = await documents()
    // Another tab removes it first.
    await api.ok(api.post(`/app/intro/documents/${doc.id}/remove`))
    await page.getByRole('button', { name: 'Remove about-me.txt' }).click()
    await expect(fileRow(page, 'about-me.txt')).toHaveCount(0, { timeout: 5_000 })
  })
})

// --- Links (refusals only: no outside fetches) -----------------------------------------------

test.describe('links', () => {
  test.beforeEach(async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
    await openTab(page, 'Files & links')
  })

  const PRIVATE = "That link points to a private or local network address, so it can't be read."
  const PORTS = 'Only links on the standard web ports (80 and 443) can be read.'
  const SCHEMES = 'Only http:// and https:// links can be read.'
  const INVALID = "That doesn't look like a valid web address."
  const LINKEDIN = /LinkedIn doesn't let apps read profiles\. Paste your About and Experience sections, or upload your LinkedIn profile PDF/
  const X = "X (Twitter) doesn't let apps read profiles without logging in. Paste your bio here instead."

  // Each group tries its links one after another on the same page.
  type Refused = { what: string; url: string; message: string | RegExp }
  const refusedGroups: Record<string, Refused[]> = {
    'private and local network addresses': [
      { what: "the app's own API", url: 'http://127.0.0.1:8103/health', message: PORTS },
      { what: 'a loopback address', url: 'http://127.0.0.1/', message: PRIVATE },
      { what: 'the cloud metadata address', url: 'http://169.254.169.254/latest/meta-data/', message: PRIVATE },
      { what: 'localhost', url: 'http://localhost/', message: PRIVATE },
      { what: 'IPv6 loopback', url: 'http://[::1]/', message: PRIVATE },
      { what: 'loopback written in hex', url: 'http://0x7f000001/', message: PRIVATE },
      { what: 'loopback written as one number', url: 'http://2130706433/', message: PRIVATE },
      { what: 'a private network address', url: 'http://10.0.0.1/admin', message: PRIVATE },
    ],
    'other schemes, odd ports and passwords in links': [
      { what: 'a file:// link', url: 'file:///etc/passwd', message: SCHEMES },
      { what: 'an ftp:// link', url: 'ftp://example.com', message: SCHEMES },
      { what: 'a non-standard port', url: 'https://example.com:8443/me', message: PORTS },
      { what: 'a link with a password in it', url: 'https://me:secret@example.com/', message: "Links with a username or password in them aren't allowed." },
    ],
    'LinkedIn and X profiles, with advice on what to do instead': [
      { what: 'a LinkedIn profile', url: 'https://www.linkedin.com/in/ananya-menon', message: LINKEDIN },
      { what: 'a LinkedIn profile typed without https', url: 'linkedin.com/in/ananya-menon', message: LINKEDIN },
      { what: 'an x.com profile', url: 'https://x.com/ananya', message: X },
      { what: 'a twitter.com profile', url: 'twitter.com/ananya', message: X },
    ],
    'text that is not a web address': [
      { what: 'words with spaces', url: 'not a link at all', message: INVALID },
      { what: 'a scheme with no host', url: 'http://', message: INVALID },
      { what: 'a javascript: link', url: 'javascript:alert(document.cookie)', message: INVALID },
    ],
  }
  for (const [group, refused] of Object.entries(refusedGroups)) {
    test(`refuses ${group} with a clear message and stores nothing`, async ({ page }) => {
      const lines = (await agentLines()).length
      const box = page.getByRole('textbox', { name: 'Link to a page about you' })
      for (const { what, url, message } of refused) {
        await test.step(`${what}: ${url}`, async () => {
          await box.fill(url)
          const response = page.waitForResponse('**/api/app/intro/links')
          await page.getByRole('button', { name: 'Add' }).click()
          expect((await response).status()).toBe(422)
          await expect(page.getByText(message)).toBeVisible()
          // What you typed stays, so you can fix it.
          await expect(box).toHaveValue(url)
          await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
          expect(await documents()).toEqual([])
          expect(await agentLines()).toHaveLength(lines)
        })
      }
    })
  }

  test('Enter submits a link too, and a blank link cannot be added', async ({ page }) => {
    const box = page.getByRole('textbox', { name: 'Link to a page about you' })
    await box.fill('   ')
    await expect(page.getByRole('button', { name: 'Add' })).toBeDisabled()
    await box.fill('http://localhost/')
    await box.press('Enter')
    await expect(page.getByText(PRIVATE)).toBeVisible()
    expect(await documents()).toEqual([])
  })

  test('the API refuses the same links when called directly', async ({ api }) => {
    for (const url of ['http://127.0.0.1:8103/health', 'http://169.254.169.254/', 'file:///etc/passwd', 'gopher://localhost:70/']) {
      const r = await api.post('/app/intro/links', { url })
      expect(r.status(), url).toBe(422)
      expect((await r.json()).detail, url).toMatch(/private or local|standard web ports|Only http/)
    }
    expect(await documents()).toEqual([])
  })

  test('an over-long link gets a plain-language message', async ({ page }) => {
    test.fail(true, 'BUG: a link over 2048 characters shows the raw validator text "String should have at most 2048 characters"')
    const box = page.getByRole('textbox', { name: 'Link to a page about you' })
    await box.fill(`https://example.com/${'a'.repeat(2100)}`)
    await page.getByRole('button', { name: 'Add' }).click()
    await expect(page.getByText('That link is too long.')).toBeVisible({ timeout: 3_000 })
    expect(await documents()).toEqual([])
  })
})

// --- Who may use these endpoints -----------------------------------------------------------

test.describe('access', () => {
  test('a signed-out visitor cannot share files or links', async ({ page, api, owner }) => {
    void owner
    await page.context().clearCookies()
    const link = await api.post('/app/intro/links', { url: 'https://example.com/' })
    expect(link.status()).toBe(401)
    const file = await page.request.post('/api/app/intro/files', {
      headers: CSRF,
      multipart: { file: { name: 'about-me.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') } },
    })
    expect(file.status()).toBe(401)
    expect((await api.get('/app/intro/call')).status()).toBe(401)
    expect(await documents()).toEqual([])
    await page.goto('/meet')
    await expect(page).toHaveURL(/\/login\?next=%2Fmeet$/)
  })

  test("someone else's account cannot see or remove the owner's files", async ({ page, api, owner }) => {
    void owner
    await api.ok(api.get('/app/onboarding'))
    const r = await page.request.post('/api/app/intro/files', {
      headers: CSRF,
      multipart: { file: { name: 'about-me.txt', mimeType: 'text/plain', buffer: Buffer.from('I lead applied AI.') } },
    })
    expect(r.ok()).toBeTruthy()
    const [doc] = await documents()

    await page.context().clearCookies()
    await api.signup(newPerson('Mallory Stranger'))
    expect((await api.get('/app/intro/documents')).status()).toBe(403)
    const remove = await api.post(`/app/intro/documents/${doc.id}/remove`)
    expect(remove.status()).toBe(403)
    expect((await remove.json()).detail).toBe('Hire Ava first.')
    expect((await api.post('/app/intro/call/answer', { question_id: 'q1', transcript: 'I am the owner now' })).status()).toBe(403)
    expect((await documents()).map((d) => d.removed)).toEqual([false])
    expect(await facts()).toEqual([])
  })

  test('a made-up or malformed document id is refused', async ({ api, owner }) => {
    void owner
    expect((await api.post(`/app/intro/documents/${randomUUID()}/remove`)).status()).toBe(404)
    expect((await api.post('/app/intro/documents/../../auth/logout/remove')).status()).toBeGreaterThanOrEqual(400)
    expect((await api.post('/app/intro/documents/not-a-uuid/remove')).status()).toBe(422)
  })

  test("requests without the app's CSRF header are refused", async ({ page, api, owner }) => {
    void owner
    expect((await api.postWithoutCsrfHeader('/app/intro/links', { url: 'https://example.com/' })).status()).toBe(403)
    const file = await page.request.post('/api/app/intro/files', {
      multipart: { file: { name: 'about-me.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') } },
    })
    expect(file.status()).toBe(403)
    expect((await api.postWithoutCsrfHeader('/app/intro/call/finish')).status()).toBe(403)
    expect(await documents()).toEqual([])
  })
})

// --- The video call ------------------------------------------------------------------------

test.describe('video call', () => {
  const ANSWERS = [
    'I lead applied AI at PebbleRoad in Singapore.',
    'Client workshops Monday to Wednesday; I am hardest to reach on Tuesday afternoons.',
    'Priya, my partner, and Arjun from my team.',
    'Never share my home address.',
  ]

  test.beforeEach(async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
  })

  test('answer every question by typing; the answers land in the notebook as "from our video call"', async ({ page, api }) => {
    await quickVoice(page)
    await page.reload()
    await expect(introTabs(page)).toBeVisible()
    await openTab(page, 'Video call')
    await expect(page.getByText('4 short questions, about five minutes.')).toBeVisible()
    // Nothing shared yet, so there is no "done" button.
    await expect(page.getByRole('button', { name: "I'm done introducing myself" })).toHaveCount(0)

    const dialog = await startCall(page)
    await expect(dialog.getByText('Question 1', { exact: true }).first()).toBeVisible()
    for (const [i, question] of CALL_QUESTIONS.entries()) {
      await typeAnswer(dialog, question, ANSWERS[i], i % 2 ? 'click' : 'enter')
      if (i < CALL_QUESTIONS.length - 1) await expect(dialog.getByText(`Question ${i + 2}`).first()).toBeVisible()
    }
    await expect(dialog).toBeHidden()
    await expect(page.getByText("Thanks for the call. Here's where I am: 4 things about you in my notebook. You can read them any time.")).toBeVisible()

    // Stored as call answers, each with its question.
    const stored = await facts()
    expect(stored.map((f) => f.source)).toEqual(['intro_call', 'intro_call', 'intro_call', 'intro_call'])
    for (const [i, answer] of ANSWERS.entries()) {
      expect(stored[i].content).toBe(`Asked "${CALL_QUESTIONS[i]}", Ananya Menon said: ${answer}`)
    }
    // The conversation keeps the questions and answers too.
    for (const answer of ANSWERS) await expect(page.getByText(answer, { exact: true })).toBeVisible()

    // Finish the introduction, then read the notebook in the dashboard.
    await openTab(page, 'Video call')
    await page.getByRole('button', { name: "I'm done introducing myself" }).click()
    await expect(page.getByText('Step 3 of 8: Your people')).toBeVisible()
    await api.answer('people', { skip: true })
    await api.answer('urgent', { topics: ['A family emergency'], digest: 'morning' })
    await api.answer('reach', { channel: 'app', quiet_start: '22:00', quiet_end: '07:00', vip_breaks_quiet: true, timezone: 'Asia/Singapore' })
    await api.answer('share', { skip: true })
    await api.answer('rules')
    await api.answer('connect', { choice: 'done' })
    await page.goto('/dashboard/notebook')
    await expect(page.getByRole('heading', { level: 1, name: "Ava's notebook" })).toBeVisible()
    const entry = page.getByRole('listitem').filter({ hasText: ANSWERS[0] })
    await expect(entry).toBeVisible()
    await expect(entry).toContainText('from our video call')
    await expect(page.getByRole('listitem').filter({ hasText: 'from our video call' })).toHaveCount(4)
  })

  test("the call also works with the browser's own voice, which headless Chromium can't play", async ({ page }) => {
    test.setTimeout(120_000) // without a voice, each line stays up long enough to read
    const dialog = await startCall(page)
    for (const [i, question] of CALL_QUESTIONS.entries()) await typeAnswer(dialog, question, ANSWERS[i])
    await expect(dialog).toBeHidden({ timeout: 30_000 })
    expect((await facts()).filter((f) => f.source === 'intro_call')).toHaveLength(4)
  })

  test('answering by voice: the words heard show as text and are saved', async ({ page }) => {
    await fakeRecognition(page)
    await quickVoice(page)
    await page.reload()
    const dialog = await startCall(page)
    await expect(dialog.getByText(CALL_QUESTIONS[0], { exact: true })).toBeVisible()
    await expect(dialog.getByText('Listening… take your time.')).toBeVisible()

    // Question 1: say it, then choose "Done answering".
    await sayAloud(page, 'I lead applied AI', false)
    await expect(dialog.getByText('I lead applied AI', { exact: true })).toBeVisible()
    await sayAloud(page, 'I lead applied AI at PebbleRoad.')
    await dialog.getByRole('button', { name: 'Done answering' }).click()
    await expect(dialog.getByText(CALL_QUESTIONS[1], { exact: true })).toBeVisible()

    // Question 2: stop talking, and the answer is sent after a short silence.
    await expect(dialog.getByText('Listening… take your time.')).toBeVisible()
    await sayAloud(page, 'Busy weeks, mostly client work.')
    await expect(dialog.getByText(CALL_QUESTIONS[2], { exact: true })).toBeVisible()

    expect((await facts()).map((f) => f.content)).toEqual([
      `Asked "${CALL_QUESTIONS[0]}", Ananya Menon said: I lead applied AI at PebbleRoad.`,
      `Asked "${CALL_QUESTIONS[1]}", Ananya Menon said: Busy weeks, mostly client work.`,
    ])
    await leaveCall(page, dialog)
  })

  test('leaving mid-call keeps the answers given so far', async ({ page }) => {
    await quickVoice(page)
    await page.reload()
    const dialog = await startCall(page)
    await typeAnswer(dialog, CALL_QUESTIONS[0], ANSWERS[0])
    await typeAnswer(dialog, CALL_QUESTIONS[1], ANSWERS[1])
    await expect(dialog.getByText(CALL_QUESTIONS[2], { exact: true })).toBeVisible()

    // Leaving asks first; "Stay" keeps the call going.
    await dialog.getByRole('button', { name: 'Leave the call' }).click()
    const confirm = dialog.getByRole('group', { name: 'Confirm leaving the call' })
    await expect(confirm).toContainText('Leave the call?')
    await confirm.getByRole('button', { name: 'Stay' }).click()
    await expect(confirm).toBeHidden()
    await expect(dialog).toBeVisible()

    await leaveCall(page, dialog)
    const stored = await facts()
    expect(stored.map((f) => f.content)).toEqual([
      `Asked "${CALL_QUESTIONS[0]}", Ananya Menon said: ${ANSWERS[0]}`,
      `Asked "${CALL_QUESTIONS[1]}", Ananya Menon said: ${ANSWERS[1]}`,
    ])
    // No "thanks for the call" for a call you left, but what you said is in the conversation.
    expect((await agentLines()).filter((l) => l.startsWith('Thanks for the call'))).toEqual([])
    await expect(page.getByText(ANSWERS[1], { exact: true })).toBeVisible()
    // And you can move on with what you gave.
    await openTab(page, 'Video call')
    await page.getByRole('button', { name: "I'm done introducing myself" }).click()
    await expect(page.getByText('Step 3 of 8: Your people')).toBeVisible()
  })

  test('leaving before answering anything stores nothing', async ({ page }) => {
    await quickVoice(page)
    await page.reload()
    const dialog = await startCall(page)
    await expect(dialog.getByText(CALL_QUESTIONS[0], { exact: true })).toBeVisible()
    await leaveCall(page, dialog)
    expect(await facts()).toEqual([])
    expect((await agentLines()).filter((l) => l.startsWith('Thanks for the call'))).toEqual([])
    await expect(page.getByRole('tab', { name: 'Video call' })).toBeVisible()
  })

  test('finishing the call twice is harmless', async ({ page, api }) => {
    await quickVoice(page)
    await page.reload()
    const dialog = await startCall(page)
    for (const [i, question] of CALL_QUESTIONS.entries()) await typeAnswer(dialog, question, ANSWERS[i])
    await expect(dialog).toBeHidden()
    await expect(page.getByText(/^Thanks for the call\./)).toBeVisible()

    // A retried request (or a second tab) finishes it again: no error, nothing lost or added.
    await api.ok(api.post('/app/intro/call/finish'))
    const view = await api.ok<{ step: string }>(api.get('/app/onboarding'))
    expect(view.step).toBe('about')
    expect(await facts()).toHaveLength(4)
    await page.reload()
    await expect(introTabs(page)).toBeVisible()
    await openTab(page, 'Video call')
    await expect(page.getByRole('button', { name: "I'm done introducing myself" })).toBeVisible()
  })

  test('finishing the call twice posts one summary, not two', async ({ api }) => {
    test.fail(true, 'BUG: every POST /app/intro/call/finish adds another "Thanks for the call…" line to the conversation')
    await api.ok(api.post('/app/intro/call/answer', { question_id: 'q1', transcript: 'I lead applied AI at PebbleRoad.' }))
    await api.ok(api.post('/app/intro/call/finish'))
    await api.ok(api.post('/app/intro/call/finish'))
    expect((await agentLines()).filter((l) => l.startsWith('Thanks for the call'))).toHaveLength(1)
  })

  test('after one answer, the summary says "1 thing", not "1 things"', async ({ api }) => {
    test.fail(true, 'BUG: Ava\'s summary says "1 things about you in my notebook"')
    await api.ok(api.post('/app/intro/call/answer', { question_id: 'q1', transcript: 'I lead applied AI at PebbleRoad.' }))
    await api.ok(api.post('/app/intro/call/finish'))
    const summary = (await agentLines()).find((l) => l.startsWith('Thanks for the call.'))
    expect(summary).toMatch(/Here's where I am: 1 thing about you in my notebook\./)
  })

  test('a blank answer cannot be sent', async ({ page }) => {
    await quickVoice(page)
    await page.reload()
    const dialog = await startCall(page)
    await expect(dialog.getByText(CALL_QUESTIONS[0], { exact: true })).toBeVisible()
    await dialog.getByRole('button', { name: 'Type instead' }).click()
    const box = dialog.getByRole('textbox', { name: 'Type your answer' })
    await box.fill('   \n  ')
    await expect(dialog.getByText('Enter to send, Shift + Enter for a new line')).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
    await box.press('Enter')
    await expect(dialog.getByText('Question 1', { exact: true }).first()).toBeVisible()
    expect(await facts()).toEqual([])
    await leaveCall(page, dialog)
  })

  test('an answer that does not go through can be sent again', async ({ page }) => {
    await quickVoice(page)
    await page.reload()
    let fail = true
    await page.route('**/api/app/intro/call/answer', (route) => (fail ? route.abort('connectionrefused') : route.continue()))
    const dialog = await startCall(page)
    await typeAnswer(dialog, CALL_QUESTIONS[0], ANSWERS[0])
    await expect(dialog.getByText(/That answer didn't go through\. Can't reach Superaide/)).toBeVisible()
    expect(await facts()).toEqual([])

    fail = false
    await dialog.getByRole('button', { name: 'Try again' }).click()
    await expect(dialog.getByText(CALL_QUESTIONS[1], { exact: true })).toBeVisible()
    expect((await facts()).map((f) => f.content)).toEqual([`Asked "${CALL_QUESTIONS[0]}", Ananya Menon said: ${ANSWERS[0]}`])
    await leaveCall(page, dialog)
  })

  test('with the camera blocked, the call explains and still works', async ({ browser, baseURL, owner }) => {
    // A fresh browser context with no permissions granted. The suite's fake media UI
    // would still say yes, so the camera refuses the way Chrome does when you block it.
    const context = await browser.newContext({ baseURL })
    try {
      const page = await context.newPage()
      await quickVoice(page)
      await cameraFails(page, 'NotAllowedError')
      const login = await context.request.post('/api/auth/login', { headers: CSRF, data: { email: owner.email, password: owner.password } })
      expect(login.ok()).toBeTruthy()
      await page.goto('/meet')
      await expect(introTabs(page)).toBeVisible()
      const dialog = await startCall(page)
      await expect(dialog.getByText('Camera blocked. Allow it for this site to show your face.')).toBeVisible()
      await expect(dialog.getByRole('button', { name: 'Turn camera on' })).toBeEnabled()
      await typeAnswer(dialog, CALL_QUESTIONS[0], ANSWERS[0])
      await expect(dialog.getByText(CALL_QUESTIONS[1], { exact: true })).toBeVisible()
      expect(await facts()).toHaveLength(1)
      await leaveCall(page, dialog)
    } finally {
      await context.close()
    }
  })

  test('with no camera at all, the call says so and still works', async ({ page }) => {
    await quickVoice(page)
    await cameraFails(page, 'NotFoundError')
    await page.reload()
    const dialog = await startCall(page)
    await expect(dialog.getByText('No camera found')).toBeVisible()
    await typeAnswer(dialog, CALL_QUESTIONS[0], ANSWERS[0])
    await expect(dialog.getByText(CALL_QUESTIONS[1], { exact: true })).toBeVisible()
    await leaveCall(page, dialog)
  })

  test('the call API refuses unknown questions and over-long answers, and ignores empty ones', async ({ api }) => {
    expect((await api.post('/app/intro/call/answer', { question_id: 'q9', transcript: 'hello' })).status()).toBe(422)
    expect((await api.post('/app/intro/call/answer', { question_id: 'q1'.repeat(10), transcript: 'hello' })).status()).toBe(422)
    expect((await api.post('/app/intro/call/answer', { question_id: 'q1', transcript: 'x'.repeat(8001) })).status()).toBe(422)
    const empty = await api.ok<{ next: { id: string } | null }>(api.post('/app/intro/call/answer', { question_id: 'q1', transcript: '   ' }))
    expect(empty.next?.id).toBe('q2')
    const last = await api.ok<{ next: unknown; reply: unknown }>(api.post('/app/intro/call/answer', { question_id: 'q4', transcript: 'Nothing to add.' }))
    expect(last).toEqual({ next: null, reply: null })
    expect((await facts()).map((f) => f.source)).toEqual(['intro_call'])
  })
})

// --- Keyboard ---------------------------------------------------------------------------

test.describe('keyboard', () => {
  test.beforeEach(async ({ page, api, owner }) => {
    void owner
    await openIntroduction(page, api)
  })

  test('the intro tabs, the file chooser and the call can be used with the keyboard alone', async ({ page }) => {
    const chat = page.getByRole('tab', { name: 'Tell me here' })
    const files = page.getByRole('tab', { name: 'Files & links' })
    const callTab = page.getByRole('tab', { name: 'Video call' })
    await expect(chat).toHaveAttribute('aria-selected', 'true')

    // Arrow keys move between the tabs (roving tabindex), and the focused tab is the selected one.
    await chat.focus()
    await page.keyboard.press('ArrowRight')
    await expect(files).toBeFocused()
    await expect(files).toHaveAttribute('aria-selected', 'true')
    await expect(chat).toHaveAttribute('aria-selected', 'false')

    // Tab into the panel and open the file chooser with Enter.
    await tabTo(page, page.getByRole('button', { name: 'choose a file' }))
    const chooser = page.waitForEvent('filechooser')
    await page.keyboard.press('Enter')
    await (await chooser).setFiles(textFile('about-me.txt', 'I lead applied AI at PebbleRoad.'))
    await expect(fileRow(page, 'about-me.txt')).toBeVisible()

    // The link box takes Enter.
    await page.getByRole('textbox', { name: 'Link to a page about you' }).focus()
    await page.keyboard.type('http://localhost/')
    await page.keyboard.press('Enter')
    await expect(page.getByText("That link points to a private or local network address, so it can't be read.")).toBeVisible()

    // Space picks the call tab, and Enter starts the call.
    await callTab.focus()
    await page.keyboard.press('Space')
    await expect(callTab).toHaveAttribute('aria-selected', 'true')
    await tabTo(page, page.getByRole('button', { name: 'Start the call' }))
    await page.keyboard.press('Enter')
    const dialog = callDialog(page)
    await expect(dialog).toBeVisible()

    // Back to "Tell me here" from the keyboard after leaving.
    await leaveCall(page, dialog)
    await chat.focus()
    await page.keyboard.press('Enter')
    await expect(chat).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('textbox', { name: /e\.g\. I run a 40-person AI consultancy/ })).toBeVisible()
  })

  test('the intro tabs follow the ARIA tabs pattern (arrow keys, a labelled tab panel)', async ({ page }) => {
    const chat = page.getByRole('tab', { name: 'Tell me here' })
    await chat.focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('tab', { name: 'Files & links' })).toBeFocused({ timeout: 2_000 })
    await expect(page.getByRole('tabpanel')).toBeVisible()
  })
})
