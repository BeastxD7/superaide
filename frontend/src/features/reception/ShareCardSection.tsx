'use client'

import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { HowThisWorks } from '@/features/dashboard/settings/HowThisWorks'
import type { ReceptionistSettings, ShareCardEdit } from '@/lib/api'

// What these rules say is decided in code (backend reception/card.py RULES), so they are shown read-only (#433).
const SHARE_CARD_RULES = [
  'Someone Ava doesn’t know is told only the values you switch on here, and your first name only if you choose it. Every switch starts off.',
  'Nothing is guessed or taken from your notes, email or calendar: she says only the exact words typed here.',
  'Ava can’t change this card herself. If you ask her in chat, she sends you to this page.',
  'She never says where you are, what you’re doing or whether you’re free, whatever the card says.',
]

// "Your share card": four values, each with its own switch (off by default), the greeting they get, and exactly what
// Ava is told. Saving a value does not switch it on: the switch is a separate, deliberate choice.
export function ShareCardSection({
  data,
  agentName,
  busy,
  onSave,
}: {
  data: ReceptionistSettings
  agentName: string
  busy: boolean
  onSave: (edit: ShareCardEdit, done: string) => Promise<boolean>
}) {
  const [draft, setDraft] = useState<Record<string, string>>({})
  const card = data.card
  const shown = (key: string, saved: string) => draft[key] ?? saved
  const dirty = Object.keys(draft).length > 0

  async function save(e: FormEvent) {
    e.preventDefault()
    if (await onSave(draft as ShareCardEdit, 'Saved. Switch a value on to let her say it.')) setDraft({})
  }

  return (
    <section id="share-card" className="scroll-mt-6 space-y-3 border-t border-line pt-4" aria-labelledby="share-card-title">
      <h3 id="share-card-title" className="text-base font-medium text-ink">
        Your share card
      </h3>
      <p className="text-sm text-body">
        What {agentName} may tell people she doesn’t know. Type a value, then switch it on. A value that’s switched off is never said.
      </p>
      {card.empty && (
        <Notice tone="info">
          Your card is empty, so {agentName} can only take a message. Add something and switch it on to let her say it.
        </Notice>
      )}
      <form onSubmit={save} className="space-y-3">
        {card.fields.map((f) => (
          <div key={f.key} className="grid gap-1.5 sm:grid-cols-[1fr_auto] sm:items-end sm:gap-3">
            <div className="space-y-1.5">
              <Label htmlFor={`card-${f.key}`}>{f.label}</Label>
              <Input
                id={`card-${f.key}`}
                type={f.key === 'email' ? 'email' : 'text'}
                maxLength={f.key === 'email' ? 200 : 120}
                value={shown(f.key, f.value)}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                autoComplete="off"
              />
            </div>
            <label className="flex items-center gap-2 pb-2 text-sm text-ink">
              <input
                type="checkbox"
                className="size-4"
                checked={f.public}
                disabled={busy || dirty}
                aria-label={`Tell people ${agentName} doesn’t know: ${f.label}`}
                onChange={(e) =>
                  void onSave(
                    { [`${f.key}_public`]: e.target.checked } as ShareCardEdit,
                    e.target.checked ? `${f.label} is now shared.` : `${f.label} is no longer shared.`,
                  )
                }
              />
              <span>{f.public ? 'Shared' : 'Not shared'}</span>
            </label>
          </div>
        ))}
        <Button type="submit" size="sm" disabled={busy || !dirty}>
          Save the card
        </Button>
        {dirty && <p className="text-xs text-muted">Save your changes before switching a value on or off.</p>}
      </form>
      <div className="rounded-xl bg-mist p-3 text-sm text-body" data-testid="share-card-preview">
        <p className="font-medium text-ink">What {agentName} is told</p>
        {card.told.length ? (
          <ul className="mt-1 list-disc pl-5">
            {card.told.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : (
          <p className="mt-1">Nothing from the card.</p>
        )}
        <p className="mt-2 font-medium text-ink">Her first words</p>
        <p className="mt-1">“{data.greeting}”</p>
      </div>
      <HowThisWorks rules={SHARE_CARD_RULES} />
    </section>
  )
}
