'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { AppError, call, type GmailStatus } from '@/lib/api'

// What came back from Google's sign-in (?gmail=… on the Capabilities page).
const RESULT: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'Gmail is connected.' },
  no_access: { tone: 'error', text: "Google didn't allow that. Try again and tick the Gmail box on Google's screen." },
  failed: { tone: 'error', text: "Google didn't connect. Try again in a minute." },
}

// The Gmail capability's own settings: sign in (read-only), then drafts, which asks Google for more.
export function GmailPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<GmailStatus>('/gmail')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setResult(new URLSearchParams(window.location.search).get('gmail'))
      })
      .catch((e) => live && setProblem(e instanceof AppError ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [])

  async function go(fn: () => Promise<{ url?: string | null } & Partial<GmailStatus>>) {
    setBusy(true)
    setProblem(null)
    try {
      const out = await fn()
      if (out.url) {
        window.location.assign(out.url) // Google's consent screen, then back here
        return
      }
      setStatus(await call<GmailStatus>('/gmail'))
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return null
  const shown = result ? RESULT[result] : undefined
  return (
    <div className="mt-3 space-y-2 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Gmail settings">
      {shown && <Notice tone={shown.tone}>{shown.text}</Notice>}
      {problem && <Notice tone="error">{problem}</Notice>}
      {!status.client_ready ? (
        <p className="text-body">Add your Google OAuth client under Connections first, then connect Gmail here.</p>
      ) : !status.connected ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body">
            {name} can read your Gmail only after you allow it on Google&apos;s screen (read-only).
          </p>
          <Button size="sm" disabled={busy} onClick={() => void go(() => call('/gmail/connect', { method: 'POST' }))}>
            Connect Gmail
          </Button>
        </div>
      ) : (
        <>
          <p className="text-body">Connected{status.address ? ` as ${status.address}` : ''}. Read-only until you turn on drafts.</p>
          <label className="flex items-start gap-2 text-body">
            <input
              type="checkbox"
              className="mt-0.5 accent-violet-600"
              checked={status.drafts}
              disabled={busy}
              onChange={(e) => {
                const enabled = e.target.checked
                void go(() => call('/gmail/drafts', { method: 'POST', json: { enabled } }))
              }}
            />
            <span>
              Let {name} save reply drafts in your Gmail. Google will ask you to allow it. Nothing is sent without your OK.
              {status.drafts && !status.drafts_allowed && (
                <span className="block text-xs text-muted">Waiting for Google&apos;s permission: untick and tick again to ask.</span>
              )}
            </span>
          </label>
        </>
      )}
    </div>
  )
}
