'use client'

import { Download, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useSchool } from '@/features/school/useSchool'
import { AppError, call, type Lesson, type LessonExport, type LessonImport } from '@/lib/api'
import { useSession } from '@/lib/session-context'

const message = (e: unknown) => (e instanceof AppError ? e.message : String(e))

// Generic lessons an owner may share, never their data. Local only: a file in, a file out.
export function SchoolPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { enabled, data, error, reload } = useSchool()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [pasted, setPasted] = useState('')

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setProblem(message(e))
    } finally {
      setBusy(false)
    }
  }

  const add = () =>
    run(async () => {
      await call<Lesson>('/school/lessons', { method: 'POST', json: { text } })
      setText('')
    })
  const consent = (l: Lesson, on: boolean) =>
    run(async () => {
      await call<Lesson>(`/school/lessons/${l.id}/consent`, { method: 'POST', json: { consent: on } })
    })
  const remove = (l: Lesson) =>
    run(async () => {
      await call(`/school/lessons/${l.id}`, { method: 'DELETE' })
    })
  const accept = (l: Lesson) =>
    run(async () => {
      await call(`/school/lessons/${l.id}/accept`, { method: 'POST', json: {} })
    })
  const exportFile = () =>
    run(async () => {
      const out = await call<LessonExport>('/school/export', { method: 'POST', json: {} })
      const blob = new Blob([JSON.stringify(out.document, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'superaide-lessons.json'
      a.click()
      URL.revokeObjectURL(url)
      setDone(
        `Saved ${out.count} lesson${out.count === 1 ? '' : 's'} to a file${out.skipped.length ? `; ${out.skipped.length} left out because they no longer pass the checks` : ''}. Open it and read it before you share it.`,
      )
    })
  const importText = (raw: string) =>
    run(async () => {
      let doc: unknown
      try {
        doc = JSON.parse(raw)
      } catch {
        throw new AppError(0, "That isn't a lesson file. Choose the .json file you were given.")
      }
      const r = await call<LessonImport>('/school/import', { method: 'POST', json: { document: doc } })
      setPasted('')
      setDone(
        `${r.added} lesson${r.added === 1 ? '' : 's'} added and waiting for you.${r.rejected.length ? ` ${r.rejected.length} not added: ${r.rejected.map((x) => x.problems.map((p) => p.message).join(' ')).join(' / ')}` : ''}`,
      )
    })

  if (enabled === false) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
          <h1 className="text-3xl font-medium text-ink tight">Ava School</h1>
          <p className="mt-2 text-body">This isn&rsquo;t turned on yet.</p>
        </div>
      </div>
    )
  }

  const lessons = data?.lessons
  const shareable = lessons?.filter((l) => l.source === 'own' && l.consented).length ?? 0

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Ava School</h1>
        <p className="mt-1 text-body">
          When you correct {name}, the general lesson can help other assistants too, and your own information never
          travels. Write lessons here, agree to share the ones you choose, and save them to a file you can read first.
          Nothing is sent anywhere, and {name} doesn&rsquo;t use a lesson to answer yet.
        </p>

        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}
        {done && (
          <div className="mt-4" role="status">
            <Notice>{done}</Notice>
          </div>
        )}

        <section aria-labelledby="new-lesson" className="mt-6 space-y-3 rounded-xl border border-line bg-white p-4">
          <h2 id="new-lesson" className="text-lg font-medium text-ink">
            Write a lesson
          </h2>
          <label htmlFor="lesson-text" className="text-sm font-medium text-ink">
            One sentence of advice for any assistant
          </label>
          <Textarea
            id="lesson-text"
            rows={3}
            maxLength={300}
            value={text}
            placeholder="When I say urgent I mean money problems, not deadlines."
            onChange={(e) => setText(e.target.value)}
          />
          <p className="text-xs text-muted">
            No names, numbers, emails or links, and nothing copied from your notes or messages: {name} checks.
          </p>
          <Button disabled={busy || text.trim().length < 12} onClick={() => void add()}>
            Add lesson
          </Button>
        </section>

        <section aria-labelledby="my-lessons" className="mt-8">
          <h2 id="my-lessons" className="text-lg font-medium text-ink">
            Your lessons
          </h2>
          <ul className="mt-2 space-y-2">
            {!lessons && !error && <Skeleton className="h-12 w-full" />}
            {lessons?.length === 0 && (
              <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
                No lessons yet.
              </li>
            )}
            {lessons?.map((l) => (
              <li key={l.id} className="rounded-xl border border-line bg-white p-3">
                <p className="text-ink">{l.text}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant="secondary">{l.source === 'own' ? 'Written by you' : l.status === 'pending' ? 'Imported, waiting for you' : 'Imported'}</Badge>
                  {l.source === 'own' && (
                    <label className="flex items-center gap-1.5 text-body">
                      <input
                        type="checkbox"
                        checked={l.consented}
                        disabled={busy}
                        onChange={(e) => void consent(l, e.target.checked)}
                      />
                      I agree to share this lesson
                    </label>
                  )}
                  {l.source === 'imported' && l.status === 'pending' && (
                    <Button size="sm" variant="outline" disabled={busy} onClick={() => void accept(l)}>
                      Keep this lesson
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    aria-label={`Delete lesson: ${l.text}`}
                    disabled={busy}
                    onClick={() => void remove(l)}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="school-files" className="mt-8 space-y-3 rounded-xl border border-line bg-white p-4">
          <h2 id="school-files" className="text-lg font-medium text-ink">
            Files
          </h2>
          <div>
            <Button variant="outline" disabled={busy || shareable === 0} onClick={() => void exportFile()}>
              <Download /> Save the {shareable} lesson{shareable === 1 ? '' : 's'} I agreed to share
            </Button>
            <p className="mt-1 text-xs text-muted">
              The file holds only the lesson text. A file you have saved can&rsquo;t be taken back, so read it before
              you share it.
            </p>
          </div>
          <div>
            <label htmlFor="lesson-file" className="text-sm font-medium text-ink">
              Read a lesson file from another assistant
            </label>
            <input
              id="lesson-file"
              type="file"
              accept="application/json,.json"
              className="mt-1 block text-sm"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) void f.text().then((raw) => importText(raw))
              }}
            />
            <label htmlFor="lesson-paste" className="mt-3 block text-sm font-medium text-ink">
              Or paste its contents
            </label>
            <Textarea id="lesson-paste" className="mt-1" rows={3} value={pasted} onChange={(e) => setPasted(e.target.value)} />
            <Button className="mt-2" variant="outline" disabled={busy || !pasted.trim()} onClick={() => void importText(pasted)}>
              <Upload /> Read it
            </Button>
            <p className="mt-1 text-xs text-muted">
              Each lesson is checked, then waits for you. {name} never applies a lesson on her own.
            </p>
          </div>
        </section>
      </div>
    </div>
  )
}
