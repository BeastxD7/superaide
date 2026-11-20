'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { call, type OutlookStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// What came back from Microsoft's sign-in (?microsoft=… on the Outlook page).
const RESULT: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'Outlook is connected.' },
  no_access: { tone: 'error', text: "Microsoft didn't grant mail access. Try again and accept the mail permissions on Microsoft's screen." },
  cancelled: { tone: 'error', text: 'The Microsoft sign-in was cancelled.' },
  expired: { tone: 'error', text: 'That sign-in took too long. Press Connect Outlook to start again.' },
  failed: { tone: 'error', text: "Microsoft didn't connect. Check the client id, the secret and the redirect address on your app, then try again." },
}

// The Outlook mail capability: save your own Microsoft app (client id and secret), sign in, and say whether she may send.
// Secrets go to the server once and are sealed in the vault; only hints come back.
export function OutlookPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<OutlookStatus | null>(null)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<OutlookStatus>('/microsoft/mail')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setResult(new URLSearchParams(window.location.search).get('microsoft'))
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function act(run: () => Promise<OutlookStatus & { url?: string | null }>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    setResult(null)
    try {
      const next = await run()
      setStatus(next)
      setSaved(done)
      if (next.url) window.location.assign(next.url) // Microsoft's consent screen for the send permission, then back here
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function connect() {
    setBusy(true)
    setProblem(null)
    try {
      const { url } = await call<{ url: string }>('/microsoft/mail/connect', { method: 'POST' })
      window.location.assign(url)
    } catch (e) {
      setProblem(errorMessage(e))
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  const shown = result ? RESULT[result] : undefined
  return (
    <div id="outlook" className="mt-3 space-y-4 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Outlook connection">
      {shown && <Notice tone={shown.tone}>{shown.text}</Notice>}
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && <Notice tone="info">{saved}</Notice>}
      {!status.client_saved ? (
        <form
          className="grid gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(async () => {
              const next = await call<OutlookStatus>('/microsoft/mail/client', { method: 'PUT', json: { client_id: clientId, client_secret: secret } })
              setSecret('')
              return next
            }, 'Your Microsoft app is saved. Now connect Outlook.')
          }}
        >
          <p className="text-body">
            In the Azure portal, register an app (Microsoft Entra, App registrations) with the redirect address{' '}
            <code>{typeof window === 'undefined' ? '' : window.location.origin}/api/app/microsoft/callback</code>, the delegated permissions Mail.Read, Mail.Send, offline_access and User.Read, and a client secret. Paste its ids here. The secret is stored encrypted and never shown again.
          </p>
          <Label htmlFor="ms-client-id">Application (client) ID</Label>
          <Input id="ms-client-id" autoComplete="off" required value={clientId} onChange={(e) => setClientId(e.target.value)} className="min-h-11" />
          <Label htmlFor="ms-client-secret">Client secret value</Label>
          <Input id="ms-client-secret" type="password" autoComplete="off" required value={secret} onChange={(e) => setSecret(e.target.value)} className="min-h-11" />
          <div>
            <Button size="sm" type="submit" disabled={busy || !clientId.trim() || !secret.trim()}>
              Save Microsoft app
            </Button>
          </div>
        </form>
      ) : !status.connected ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body">{name} can read your Outlook mail only after you allow it on Microsoft&apos;s screen.</p>
          <Button size="sm" disabled={busy} onClick={() => void connect()}>
            Connect Outlook
          </Button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body">
              Connected as <strong>{status.address}</strong>.
            </p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call<OutlookStatus>('/microsoft/mail', { method: 'DELETE' }), 'Outlook is disconnected.')}>
              Disconnect
            </Button>
          </div>
          <label className="flex min-h-11 items-center gap-2 text-body">
            <input
              type="checkbox"
              role="switch"
              className="size-5 accent-violet-600"
              checked={status.sending}
              disabled={busy}
              onChange={(e) =>
                void act(
                  () => call<OutlookStatus>('/microsoft/mail/sending', { method: 'POST', json: { enabled: e.target.checked } }),
                  e.target.checked ? 'Sending is on: every reply or email waits for your OK.' : 'Sending is off.',
                )
              }
            />
            Let {name} propose replies and new emails (each waits for your OK)
          </label>
        </>
      )}
    </div>
  )
}
