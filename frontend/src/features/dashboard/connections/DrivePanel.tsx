'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { call, type DriveStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// What came back from Google's sign-in (?drive=… on the Google Drive page).
const RESULT: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'Google Drive is connected.' },
  no_access: { tone: 'error', text: "Google didn't allow that. Try again and tick the Drive box on Google's screen." },
  api_disabled: {
    tone: 'error',
    text: "The Google Drive API isn't switched on for your Google project. In Google Cloud open APIs & Services, Library, search Google Drive API, press Enable (and Google Docs API if you use drafts), then try again.",
  },
  failed: { tone: 'error', text: "Google didn't connect. Try again in a minute." },
}

// The Google Drive capability's connection: sign in read-only. Drafts (one more permission) are on their own page.
export function DrivePanel({ name }: { name: string }) {
  const [status, setStatus] = useState<DriveStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<DriveStatus>('/drive')
      .then((s) => {
        if (!live) return
        setStatus(s)
        setResult(new URLSearchParams(window.location.search).get('drive'))
      })
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function connect() {
    setBusy(true)
    setProblem(null)
    try {
      const { url } = await call<{ url: string }>('/drive/connect', { method: 'POST' })
      window.location.assign(url) // Google's consent screen, then back here
    } catch (e) {
      setProblem(errorMessage(e))
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  const shown = result ? RESULT[result] : undefined
  return (
    <div id="drive" className="mt-3 space-y-2 rounded-lg bg-mist p-3 text-sm" role="group" aria-label="Google Drive connection">
      {shown && <Notice tone={shown.tone}>{shown.text}</Notice>}
      {problem && <Notice tone="error">{problem}</Notice>}
      {!status.client_ready ? (
        <p className="text-body">Add your Google OAuth client under Capabilities first (the Google account card), then connect Google Drive here.</p>
      ) : !status.connected ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-body">{name} can read your Drive only after you allow it on Google&apos;s screen (read-only).</p>
          <Button size="sm" disabled={busy} onClick={() => void connect()}>
            Connect Google Drive
          </Button>
        </div>
      ) : (
        <p className="text-body">
          Connected, read-only. {status.drafts ? `Drafts are on: every new file waits for your OK.` : 'Turn drafts on from the Drafts page if you want Ava to propose files.'}
        </p>
      )}
    </div>
  )
}
