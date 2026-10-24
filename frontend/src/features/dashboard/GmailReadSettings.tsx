'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { SettingRow } from '@/features/dashboard/settings/SettingRow'
import { SettingsSection } from '@/features/dashboard/settings/SettingsSection'
import { Button } from '@/components/ui/button'
import { call, type GmailReadSettings as Settings, type GmailReadView } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { StepUpDialog } from '@/features/dashboard/StepUpDialog'

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
const EXAMPLES: Record<string, string> = {
  payment_preview: 'Example: a card alert from your bank shows its sender and subject, or also its first lines.',
  numbers: 'Example: "Rs 4,500.00 at TEAHOUSE" is quoted; "card 4111 1111 1111 1111" always becomes [number].',
  codes: 'Example: "Your code is 834201". Off by default: you confirm it first.',
}

// Everything that limits what Ava reads in Gmail, as the owner's own settings (#433). The tools enforce these.
export function GmailReadSettings({ name }: { name: string }) {
  const [view, setView] = useState<GmailReadView | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
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

  async function save(patch: Partial<Settings> & { confirmation?: string; confirm_password?: string }, what: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    try {
      const v = await call<GmailReadView>('/gmail/read-settings', { method: 'PUT', json: patch })
      setView(v)
      setAsking(false)
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
  const audit = (key: string) => view.last_changed?.[key] ?? null

  const choiceRow = (key: string) => {
    const c = view.choices[key]
    if (!c) return null
    const value = (s as unknown as Record<string, string | number>)[key]
    const isCodes = key === 'codes'
    return (
      <SettingRow
        key={key}
        title={c.label}
        why={c.help}
        example={EXAMPLES[key]}
        status={value === c.default ? 'Default' : 'Changed'}
        audit={audit(key)}
        field
        control={({ id, describedBy }) => (
          <select
            id={id}
            aria-describedby={describedBy}
            className="min-h-11 max-w-full rounded-md border border-line bg-white px-2 py-1.5 text-base md:text-sm"
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
        )}
      />
    )
  }

  const listRow = (key: 'blocked' | 'allowed') => (
    <SettingRow
      key={key}
      title={key === 'blocked' ? 'Never read these senders, domains or labels' : 'Always allow these senders'}
      why={
        key === 'blocked'
          ? `One per line. ${name} will not see them at all.`
          : 'One per line. Their payment mail may be previewed and amounts quoted. One-time codes stay hidden.'
      }
      example={key === 'blocked' ? 'jane@example.com, @clinic.example or label:Taxes' : 'billing@vendor.example'}
      status={s[key].length ? `${s[key].length} on the list` : 'Empty'}
      audit={audit(key)}
      field
      control={({ id, describedBy }) => (
        <div className="w-full min-w-0 space-y-2 sm:w-72">
          <textarea
            id={id}
            aria-describedby={describedBy}
            rows={3}
            className="w-full rounded-md border border-line px-2 py-1.5 text-base md:text-sm"
            value={lists[key]}
            onChange={(e) => setLists({ ...lists, [key]: e.target.value })}
          />
          <Button
            size="sm"
            variant="outline"
            className="touch:min-h-11"
            disabled={busy}
            onClick={() => void save({ [key]: split(lists[key]) } as Partial<Settings>, key === 'blocked' ? 'Never-read list' : 'Allow list')}
          >
            Save list
          </Button>
        </div>
      )}
    />
  )

  return (
    <section id="gmail-read" className="min-w-0" role="group" aria-label="What Ava may read in Gmail">
      <p className="mb-3 text-sm text-body">
        These are your rules. They change what {name} can see in your mail; she tells you which one when something is hidden.
      </p>
      {problem && (
        <div className="mb-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      {(s.payment_preview === 'hide' || s.numbers === 'blanked') && (
        <div className="mb-3">
          <Notice tone="info">
            {name} can&apos;t read amounts in payment emails right now. Change &ldquo;Payment and transaction emails&rdquo; and
            &ldquo;Numbers in emails&rdquo; below to let her.
          </Notice>
        </div>
      )}
      {saved && <p role="status" className="mb-3 text-xs text-emerald-700">{saved}</p>}
      <SettingsSection title="Payment and account mail" description="How much of bank and payment emails Ava may see.">
        {['payment_preview', 'bank_change_preview', 'numbers', 'codes'].map(choiceRow)}
      </SettingsSection>
      <SettingsSection title="How much she reads" description="Sizes and how far back she looks.">
        {['body_chars', 'search_limit', 'thread_depth', 'lookback_days'].map(choiceRow)}
      </SettingsSection>
      <SettingsSection title="Senders" description="People and labels that are always off limits or always allowed.">
        {(['blocked', 'allowed'] as const).map(listRow)}
      </SettingsSection>
      {asking && (
        <StepUpDialog
          action="gmail_codes_read"
          title="Let Ava read one-time codes and password resets?"
          warning={`Mail about one-time codes and password resets would no longer be hidden from ${name}. If anything in your mail tricks her, she could repeat a code or a reset link. Turn this on only if you need it.`}
          example='Example: a "Your login code is 834201" email, or a "Reset your bank password" link. Anyone who gets that code can sign in as you.'
          confirmLabel="Confirm and turn on"
          onConfirmed={(confirmation, password) => save({ codes: 'read', confirmation, confirm_password: password }, 'One-time codes and password resets')}
          onCancel={() => setAsking(false)}
        />
      )}
    </section>
  )
}
