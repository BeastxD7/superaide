'use client'

import { useEffect, useState } from 'react'
import { Input } from '@/components/ui/input'
import { call } from '@/lib/api'

export type Look = { avatar: boolean; mark: boolean; title: string; accent: string }

const SWATCH: Record<string, string> = {
  indigo: '#4f46e5',
  teal: '#0f766e',
  slate: '#334155',
  rose: '#be123c',
  amber: '#b45309',
}

// How the email signature looks: picture on or off, the "Sent with Superaide" line, an own title line and an accent
// from a safe palette, with a live preview. The preview is the block the backend builds (nothing is saved or sent) and
// is shown in a sandboxed frame, so nothing in it can run.
export function SignatureLook({
  look,
  accents,
  onChange,
  draft,
  disabled,
}: {
  look: Look
  accents: string[]
  onChange: (next: Look) => void
  draft: { first_name: string; initial: string; wording: string }
  disabled?: boolean
}) {
  const [html, setHtml] = useState('')
  const key = JSON.stringify([look, draft])
  useEffect(() => {
    const t = setTimeout(() => {
      call<{ html: string }>('/principal-card/preview', { method: 'POST', json: { ...look, ...draft } }).then(
        (r) => setHtml(r.html),
        () => setHtml(''),
      )
    }, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return (
    <section aria-labelledby="card-look" className="mt-6 space-y-3 rounded-xl border border-line bg-white p-4">
      <h2 id="card-look" className="text-lg font-medium text-ink">
        How the signature looks
      </h2>
      <label className="flex items-start gap-2 text-sm text-ink">
        <input
          type="checkbox"
          className="mt-0.5 size-4"
          checked={look.avatar}
          disabled={disabled}
          onChange={(e) => onChange({ ...look, avatar: e.target.checked })}
        />
        <span>Show the small picture</span>
      </label>
      <label className="flex items-start gap-2 text-sm text-ink">
        <input
          type="checkbox"
          className="mt-0.5 size-4"
          checked={look.mark}
          disabled={disabled}
          onChange={(e) => onChange({ ...look, mark: e.target.checked })}
        />
        <span>Add a small &ldquo;Sent with Superaide&rdquo; line (off by default)</span>
      </label>
      <label htmlFor="card-title" className="block text-sm font-medium text-ink">
        Your own title line (optional)
      </label>
      <Input
        id="card-title"
        value={look.title}
        maxLength={60}
        placeholder="Personal assistant of {name}"
        disabled={disabled}
        onChange={(e) => onChange({ ...look, title: e.target.value })}
      />
      <fieldset>
        <legend className="text-sm font-medium text-ink">Button colour</legend>
        <div className="mt-1 flex flex-wrap gap-2">
          {accents.map((a) => (
            <label key={a} className="flex items-center gap-1.5 text-sm text-ink">
              <input
                type="radio"
                name="card-accent"
                value={a}
                checked={look.accent === a}
                disabled={disabled}
                onChange={() => onChange({ ...look, accent: a })}
              />
              <span className="inline-block size-4 rounded-full border border-line" style={{ background: SWATCH[a] ?? '#4f46e5' }} aria-hidden />
              <span className="capitalize">{a}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div>
        <p className="text-sm font-medium text-ink">Preview</p>
        <iframe
          title="Email signature preview"
          sandbox=""
          srcDoc={html}
          className="mt-1 h-56 w-full max-w-[640px] rounded-md border border-line bg-white"
          data-testid="signature-preview"
        />
        <p className="mt-1 text-xs text-muted">
          Light-mode look. Mail apps may show it a little differently; if pictures are blocked, the name and the verify link still read.
        </p>
      </div>
    </section>
  )
}
