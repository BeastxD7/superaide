'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type View = {
  settings: Record<string, string | number>
  choices: Record<string, { values: (string | number)[]; default: string | number; label: string; help: string; unit?: string }>
}

const optionText = (v: string | number, unit?: string) => (unit ? `${v} ${unit}` : String(v))

// The owner's limits for one capability (Calendar, Web, ...): each one a labelled choice with what it means (#433).
// `path` is the capability's /app route prefix, e.g. "calendar".
export function ConnectorSettings({ path, title, anchor }: { path: string; title: string; anchor: string }) {
  const [view, setView] = useState<View | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    call<View>(`/${path}/read-settings`)
      .then((v) => live && setView(v))
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [path])

  async function save(key: string, value: string | number, label: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      setView(await call<View>(`/${path}/read-settings`, { method: 'PUT', json: { [key]: value } }))
      setSaved(`${label} saved.`)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!view) return problem ? <Notice tone="error">{problem}</Notice> : null
  return (
    <section id={anchor} className="mt-3 min-w-0 space-y-3 rounded-lg border border-line p-3 text-sm" role="group" aria-label={title}>
      <div>
        <p className="font-medium text-ink">{title}</p>
        <p className="text-xs text-muted">These are your rules. Ava tells you which one when something is limited.</p>
      </div>
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && (
        <p role="status" className="text-xs text-emerald-700">
          {saved}
        </p>
      )}
      {Object.entries(view.choices).map(([key, c]) => {
        const id = `${anchor}-${key}`
        return (
          <div key={key}>
            <label htmlFor={id} className="block font-medium text-ink">
              {c.label}
            </label>
            <p className="text-xs text-muted">{c.help}</p>
            <select
              id={id}
              className="mt-1 max-w-full rounded-md border border-line bg-white px-2 py-1.5"
              value={String(view.settings[key])}
              disabled={busy}
              onChange={(e) => void save(key, typeof c.values[0] === 'number' ? Number(e.target.value) : e.target.value, c.label)}
            >
              {c.values.map((v) => (
                <option key={String(v)} value={String(v)}>
                  {optionText(v, c.unit)}
                  {v === c.default ? ' (default)' : ''}
                </option>
              ))}
            </select>
          </div>
        )
      })}
    </section>
  )
}
