'use client'

import { useEffect, useRef, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Issued = { nonce: string; method: 'confirm' | 'password'; expires_in: number; what: string }

// The confirmation every sensitive change goes through (#451): a warning, a real example, "I understand".
// Cancel has the focus. Confirm asks the server for a single-use confirmation (2 minutes, tied to this session and
// this action) and hands it to `onConfirmed`, which sends it with the change. With STEPUP_MODE=password the server
// also wants the account password; Google-only accounts are never asked for one.
export function StepUpDialog({
  action,
  title,
  warning,
  example,
  confirmLabel,
  onConfirmed,
  onCancel,
}: {
  action: string
  title: string
  warning: string
  example: string
  confirmLabel: string
  onConfirmed: (confirmation: string, password?: string) => Promise<void>
  onCancel: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const [understood, setUnderstood] = useState(false)
  const [needsPassword, setNeedsPassword] = useState(false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
    d?.querySelector<HTMLButtonElement>('[data-cancel]')?.focus() // the safe choice has the focus
    return () => d?.close()
  }, [])

  async function confirm() {
    setBusy(true)
    setProblem(null)
    try {
      const issued = await call<Issued>('/stepup', { method: 'POST', json: { action } })
      if (issued.method === 'password' && !password) {
        setNeedsPassword(true)
        return
      }
      await onConfirmed(issued.nonce, issued.method === 'password' ? password : undefined)
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby="stepup-title"
      aria-describedby="stepup-warning"
      className="m-auto w-[min(92vw,28rem)] rounded-xl border border-amber-300 bg-white p-4 text-sm text-ink shadow-xl backdrop:bg-black/40"
      onCancel={(e) => {
        e.preventDefault()
        onCancel()
      }}
    >
      <h2 id="stepup-title" className="text-base font-semibold">
        {title}
      </h2>
      <p id="stepup-warning" className="mt-2 text-body">
        {warning}
      </p>
      <p className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-900">{example}</p>
      <label className="mt-3 flex items-start gap-2">
        <input type="checkbox" className="mt-0.5 accent-violet-600" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
        <span>I understand</span>
      </label>
      {needsPassword && (
        <div className="mt-2">
          <label htmlFor="stepup-password" className="block text-xs text-muted">
            Your password (your settings ask for it too)
          </label>
          <input
            id="stepup-password"
            type="password"
            autoComplete="current-password"
            className="mt-1 w-full rounded-md border border-line px-2 py-1.5"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      )}
      {problem && (
        <div className="mt-2">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button data-cancel size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={!understood || busy || (needsPassword && !password)} onClick={() => void confirm()}>
          {confirmLabel}
        </Button>
      </div>
    </dialog>
  )
}
