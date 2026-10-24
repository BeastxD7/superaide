'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { GoogleCalendarRow, type GoogleGuide, type GoogleService } from '@/features/workspace/GoogleCalendar'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Status = { services: { google: GoogleService }; guides: { google: GoogleGuide }; keys_kept_in: string }

// Where the vault's master key lives (backend vault/crypto.py), in words.
const KEY_PLACES: Record<string, string> = {
  keychain: 'your Mac’s Keychain',
  environment: 'the server’s vault',
  'secret file': 'the server’s vault',
}

// The one Google sign-in Gmail and Calendar both use: your own OAuth client, then "Sign in with Google". It used to sit on
// Connections; it belongs with the capabilities it serves (docs/design/dashboard-ia.md). Google sends the browser back here
// with ?google=<outcome>, shown once and then tidied away.
export function GoogleAccountCard({ onChanged }: { onChanged?: () => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let live = true
    call<Status>('/connections')
      .then((s) => {
        if (!live) return
        setStatus(s)
        const params = new URLSearchParams(window.location.search)
        const result = params.get('google')
        if (result) {
          // Only say "connected" if Google really is: anyone can send a link with ?google=connected.
          if (!['connected', 'actions-enabled'].includes(result) || s.services.google.connected) {
            setOutcome(result)
            setOpen(true)
          }
          params.delete('google')
          const rest = params.toString()
          window.history.replaceState(window.history.state, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
        }
      })
      .catch((e: unknown) => live && setError(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  if (!status) return error ? <Notice tone="error">{error}</Notice> : null
  return (
    <section id="google" aria-labelledby="google-account-title" className="mt-6">
      <h2 id="google-account-title" className="text-lg font-medium text-ink">
        Google account
      </h2>
      <p className="mb-2 text-sm text-body">One sign-in for Gmail and Google Calendar. Set it up here, then turn each capability on below.</p>
      <GoogleCalendarRow
        service={status.services.google}
        guide={status.guides.google}
        where={KEY_PLACES[status.keys_kept_in] ?? 'a private key file'}
        expanded={open}
        onToggle={() => setOpen((o) => !o)}
        outcome={outcome}
        onChanged={(next) => {
          setOutcome(null)
          onChanged?.() // the capabilities below say whether Google is connected: they read it again
          setStatus((prev) => (prev ? { ...prev, services: { ...prev.services, google: next.services.google } } : prev))
        }}
      />
    </section>
  )
}
