'use client'

import { Download, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { ActionError } from '@/components/load-state'
import { Button } from '@/components/ui/button'
import { useAppData } from '@/features/dashboard/data'
import { API_URL } from '@/lib/http'
import { call, uploadForm, type PassportImport, type PassportPreview } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// The memory passport: take everything Ava knows with you, or bring memory in. Preview first, erase later.
export function PassportPanel({ name, onChanged }: { name: string; onChanged: () => unknown }) {
  const { data, reload } = useAppData<{ imports: PassportImport[] }>('/passport/imports')
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PassportPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [confirmErase, setConfirmErase] = useState<string | null>(null)

  function reset() {
    setFile(null)
    setPreview(null)
    if (input.current) input.current.value = ''
  }

  async function choose(f: File | null) {
    setError(null)
    setDone(null)
    setPreview(null)
    setFile(f)
    if (!f) return
    setBusy(true)
    try {
      setPreview(await uploadForm<PassportPreview>('/passport/import', f, { confirm: 'false' }))
    } catch (e) {
      setError(errorMessage(e))
      setFile(null)
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const r = await uploadForm<PassportPreview>('/passport/import', file, { confirm: 'true' })
      setDone(`Added ${r.new_facts} ${r.new_facts === 1 ? 'fact' : 'facts'} to ${name}'s notebook.`)
      reset()
      await Promise.all([reload(), onChanged()])
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function erase(id: string) {
    setError(null)
    try {
      await call(`/passport/imports/${id}/erase`, { method: 'POST' })
      setConfirmErase(null)
      setDone('Import erased.')
      await Promise.all([reload(), onChanged()])
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const live = (data?.imports ?? []).filter((i) => !i.erased)

  return (
    <section aria-labelledby="passport-h" className="mt-6 rounded-xl border border-line bg-white p-4">
      <h2 id="passport-h" className="text-base font-medium text-ink">
        Memory passport
      </h2>
      <p className="mt-1 text-sm text-body">
        Take everything {name} knows about you as a file you own, or bring memory in from another assistant. Imported
        notes are private to you and never replace what {name} already knows.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button asChild size="sm" variant="outline">
          <a href={`${API_URL}/app/passport/export`} download>
            <Download className="size-4" aria-hidden /> Download passport
          </a>
        </Button>
        <Button size="sm" variant="outline" onClick={() => input.current?.click()} disabled={busy}>
          <Upload className="size-4" aria-hidden /> Import a passport
        </Button>
        <input
          ref={input}
          type="file"
          accept=".zip,.jsonl,application/zip"
          className="sr-only"
          aria-label="Passport file"
          onChange={(e) => void choose(e.target.files?.[0] ?? null)}
        />
      </div>

      {error && (
        <div className="mt-3">
          <ActionError action="use that passport" error={error} />
        </div>
      )}
      {done && (
        <p role="status" className="mt-3 text-sm text-ok">
          {done}
        </p>
      )}

      {preview && (
        <div className="mt-4 rounded-lg border border-line bg-surface p-3 text-sm" role="group" aria-label="Import preview">
          <p className="font-medium text-ink">
            {preview.new_facts} new {preview.new_facts === 1 ? 'fact' : 'facts'} from {preview.new_sources}{' '}
            {preview.new_sources === 1 ? 'source' : 'sources'}
            {preview.new_people > 0 && `, ${preview.new_people} ${preview.new_people === 1 ? 'person' : 'people'}`}.
            Import?
          </p>
          <p className="mt-1 text-muted">
            {preview.already_known_facts > 0 && `${preview.already_known_facts} ${name} already knows. `}
            {preview.skipped_count > 0 && `${preview.skipped_count} could not be read and will be left out. `}
            They arrive at low confidence, visible only to you.
          </p>
          {preview.skipped.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-muted">
              {preview.skipped.slice(0, 3).map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
          {preview.samples.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-xs text-body">
              {preview.samples.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={() => void confirm()} disabled={busy || preview.new_facts + preview.new_sources + preview.new_people === 0}>
              Import
            </Button>
            <Button size="sm" variant="ghost" onClick={reset}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {live.length > 0 && (
        <ul className="mt-4 space-y-2" aria-label="Imports">
          {live.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="min-w-0 flex-1 text-body [overflow-wrap:anywhere]">
                {i.label} · {i.facts_left} {i.facts_left === 1 ? 'fact' : 'facts'} ·{' '}
                {new Date(i.at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}
              </span>
              {confirmErase === i.id ? (
                <>
                  <span className="text-xs text-muted">Deletes everything this import brought.</span>
                  <Button size="sm" variant="destructive" onClick={() => void erase(i.id)}>
                    Erase import
                  </Button>
                  <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirmErase(null)}>
                    Keep
                  </Button>
                </>
              ) : (
                <button onClick={() => setConfirmErase(i.id)} className="text-xs font-medium text-muted hover:text-danger">
                  Erase this import
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
