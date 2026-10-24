'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { call, type GmailReadSettings as Settings, type GmailReadView } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// What each choice means, in the owner's words (the backend sends the label and one-line help).
const VALUE_LABEL: Record<string, string> = {
  hide: 'Hide previews',
  show: 'Show previews',
  blanked: 'Blank long numbers',
  amounts: 'Keep amounts, blank card and account numbers',
  never: 'Never show',
  read: 'Let Ava read them',
  '0': 'All of my mail',
}
const optionText = (name: string, v: string | number) => {
  if (VALUE_LABEL[String(v)] && name !== 'lookback_days') return VALUE_LABEL[String(v)]
  if (name === 'lookback_days') return v === 0 ? 'All of my mail' : `Last ${v} days`
  if (name === 'body_chars') return `${v} characters`
  return String(v)
}
const ORDER = ['payment_preview', 'bank_change_preview', 'numbers', 'codes', 'body_chars', 'search_limit', 'thread_depth', 'lookback_days']
const EXAMPLES: Record<string, string> = {
  payment_preview: 'Example: a card alert from your bank shows its sender and subject, or also its first lines.',
  numbers: 'Example: "Rs 4,500.00 at TEAHOUSE" is quoted; "card 4111 1111 1111 1111" always becomes [number].',
  codes: 'Example: "Your code is 834201". Off by default: a malicious email could try to make Ava repeat it.',
}

// Everything that limits what Ava reads in Gmail, as the owner's own settings (#433). The tools enforce these.
export function GmailReadSettings({ name }: { name: string }) {
  const [view, setView] = useState<GmailReadView | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [password, setPassword] = useState('')
  const [asking, setAsking] = useState(false)
  const [lists, setLists] = useState<{ blocked: string; allowed: string }>({ blocked: '', allowed: '' })

  useEffect(() => {
    let live = true
    call<GmailReadView>('/gmail/read-settings')
      .then((v) => {
        if (!live) return
        setView(v)
        setLists({ blocked: v.settings.blocked.join('\n'), allowed: v.settings.allowed.join('\n') })
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function save(patch: Partial<Settings> & { confirm_password?: string }, what: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      const v = await call<GmailReadView>('/gmail/read-settings', { method: 'PUT', json: patch })
      setView(v)
      setAsking(false)
      setPassword('')
      setSaved(`${what} saved.`)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!view) return problem ? <Notice tone="error">{problem}</Notice> : null
  const s = view.settings
  const split = (text: string) => text.split('\n').map((x) => x.trim()).filter(Boolean)
  return (
    <section
      id="gmail-read"
      className="mt-3 min-w-0 space-y-3 rounded-lg border border-line p-3 text-sm"
      role="group"
      aria-label="What Ava may read in Gmail"
    >
      <div>
        <p className="font-medium text-ink">What {name} may read</p>
        <p className="text-xs text-muted">
          These are your rules. They change what {name} can see in your mail; she tells you which one when something is hidden.
        </p>
      </div>
      {problem && <Notice tone="error">{problem}</Notice>}
      {(s.payment_preview === 'hide' || s.numbers === 'blanked') && (
        <Notice tone="info">
          {name} can&apos;t read amounts in payment emails right now. Change &ldquo;Payment and transaction emails&rdquo; and
          &ldquo;Numbers in emails&rdquo; below to let her.
        </Notice>
      )}
      {saved && <p role="status" className="text-xs text-emerald-700">{saved}</p>}
      {ORDER.map((key) => {
        const c = view.choices[key]
        if (!c) return null
        const id = `gmail-read-${key}`
        const value = (s as unknown as Record<string, string | number>)[key]
        const isCodes = key === 'codes'
        return (
          <div key={key}>
            <label htmlFor={id} className="block font-medium text-ink">
              {c.label}
            </label>
            <p className="text-xs text-muted">{c.help}</p>
            {EXAMPLES[key] && <p className="text-xs text-muted">{EXAMPLES[key]}</p>}
            <select
              id={id}
              className="mt-1 max-w-full rounded-md border border-line bg-white px-2 py-1.5"
              value={String(value)}
              disabled={busy}
              onChange={(e) => {
                const raw = e.target.value
                const next = typeof c.values[0] === 'number' ? Number(raw) : raw
                if (isCodes && next === 'read') {
                  setAsking(true)
                  return
                }
                void save({ [key]: next } as Partial<Settings>, c.label)
              }}
            >
              {c.values.map((v) => (
                <option key={String(v)} value={String(v)}>
                  {optionText(key, v)}
                  {v === c.default ? ' (default)' : ''}
                </option>
              ))}
            </select>
            {isCodes && asking && (
              <div className="mt-2 space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2" role="alertdialog" aria-label="Confirm">
                <p className="text-xs font-medium text-amber-900">
                  Careful: one-time codes and password resets are secrets. If you allow this, anything in your mail that
                  tricks {name} could make her repeat one. Enter your password to turn it on.
                </p>
                <input
                  type="password"
                  aria-label="Your password"
                  autoComplete="current-password"
                  className="w-full rounded-md border border-line px-2 py-1.5"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <div className="flex gap-2">
                  <Button size="sm" disabled={busy || !password} onClick={() => void save({ codes: 'read', confirm_password: password }, c.label)}>
                    Turn on
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => { setAsking(false); setPassword('') }}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      {(['blocked', 'allowed'] as const).map((key) => (
        <div key={key}>
          <label htmlFor={`gmail-read-${key}`} className="block font-medium text-ink">
            {key === 'blocked' ? 'Never read these senders, domains or labels' : 'Always allow these senders'}
          </label>
          <p className="text-xs text-muted">
            {key === 'blocked'
              ? `One per line: jane@example.com, @clinic.example or label:Taxes. ${name} will not see them at all.`
              : 'One per line. Their payment mail may be previewed and amounts quoted. One-time codes stay hidden.'}
          </p>
          <textarea
            id={`gmail-read-${key}`}
            rows={3}
            className="mt-1 w-full rounded-md border border-line px-2 py-1.5"
            value={lists[key]}
            onChange={(e) => setLists({ ...lists, [key]: e.target.value })}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void save({ [key]: split(lists[key]) } as Partial<Settings>, key === 'blocked' ? 'Never-read list' : 'Allow list')}
          >
            Save list
          </Button>
        </div>
      ))}
    </section>
  )
}
