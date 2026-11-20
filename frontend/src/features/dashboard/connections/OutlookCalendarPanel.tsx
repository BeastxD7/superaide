'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { call, type OutlookCalendarStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'
import { MicrosoftAppForm } from '@/features/dashboard/connections/OutlookPanel'

// What came back from Microsoft's sign-in (?microsoft=… on the Outlook calendar page).
const RESULT: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'The Outlook calendar is connected.' },
  no_access: { tone: 'error', text: "Microsoft didn't grant calendar access. Try again and accept the calendar permissions on Microsoft's screen." },
  cancelled: { tone: 'error', text: 'The Microsoft sign-in was cancelled.' },
  expired: { tone: 'error', text: 'That sign-in took too long. Press Connect Outlook calendar to start again.' },
  failed: { tone: 'error', text: "Microsoft didn't connect. Check the client id, the secret and the redirect address on your app, then try again." },
}

// The Outlook calendar capability: the Microsoft app (shared with Outlook mail), sign in, and whether she may add events.
export function OutlookCalendarPanel({ name }: { name: string }) {
  const [status, setStatus] = useState<OutlookCalendarStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<OutlookCalendarStatus>('/microsoft/calendar')
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

  async function act(run: () => Promise<OutlookCalendarStatus & { url?: string | null }>, done: string) {
    setBusy(true)
    setProblem(null)
    setSaved(null)
    setResult(null)
    try {
      const next = await run()
      setStatus(next)
      setSaved(done)
      if (next.url) window.location.assign(next.url) // Microsoft's consent screen for the change permission, then back here
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
      const { url } = await call<{ url: string }>('/microsoft/calendar/connect', { method: 'POST' })
      window.location.assign(url)
    } catch (e) {
      setProblem(errorMessage(e))
      setBusy(false)
    }
  }

  async function saveApp(clientId: string, secret: string) {
    await act(async () => {
      await call('/microsoft/mail/client', { method: 'PUT', json: { client_id: clientId, client_secret: secret } })
      return call<OutlookCalendarStatus>('/microsoft/calendar')
    }, 'Your Microsoft app is saved. Now connect the calendar.')
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  const shown = result ? RESULT[result] : undefined
  return (
    <div id="outlook-calendar" className="mt-3 space-y-4 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Outlook calendar connection">
      {shown && <Notice tone={shown.tone}>{shown.text}</Notice>}
      {problem && <Notice tone="error">{problem}</Notice>}
      {saved && <Notice tone="info">{saved}</Notice>}
      {!status.client_saved ? (
        <MicrosoftAppForm busy={busy} onSave={saveApp} />
      ) : !status.connected ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body">{name} can read your Outlook calendar only after you allow it on Microsoft&apos;s screen.</p>
          <Button size="sm" disabled={busy} onClick={() => void connect()}>
            Connect Outlook calendar
          </Button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-body">
              Connected as <strong>{status.address}</strong>.
            </p>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call<OutlookCalendarStatus>('/microsoft/calendar', { method: 'DELETE' }), 'The Outlook calendar is disconnected.')}>
              Disconnect
            </Button>
          </div>
          <label className="flex min-h-11 items-center gap-2 text-body">
            <input
              type="checkbox"
              role="switch"
              className="size-5 accent-violet-600"
              checked={status.changes}
              disabled={busy}
              onChange={(e) =>
                void act(
                  () => call<OutlookCalendarStatus>('/microsoft/calendar/changes', { method: 'POST', json: { enabled: e.target.checked } }),
                  e.target.checked ? 'Changes are on: every new event waits for your OK, and nobody is invited.' : 'Changes are off.',
                )
              }
            />
            Let {name} propose new events (each waits for your OK)
          </label>
        </>
      )}
    </div>
  )
}
