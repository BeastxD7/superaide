'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Notice } from '@/components/site'
import { errorMessage } from '@/lib/action'
import { call } from '@/lib/api'

export type SetupResult = { name: string; status: 'ok' | 'warn' | 'fail'; cause: string; fix: string }
export type SetupReport = { results: SetupResult[]; fails: number; warns: number; ok: boolean }

const LABEL = { ok: 'Fine', warn: 'Look at this', fail: 'Fix this' } as const

// The photo default (#511): the row says photos go to Claude, and this is the one-click way to stop it.
function PhotosOff({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function off() {
    setBusy(true)
    try {
      await call('/media-providers/vision/remove', { method: 'POST', json: {} })
      onDone()
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }
  return (
    <div className="mt-2">
      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void off()}>
        Turn off photo reading
      </Button>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  )
}

// "Check my setup" (#567): runs the in-app checks from the server's doctor and says how to fix each problem.
export function SetupCheckCard() {
  const [report, setReport] = useState<SetupReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setReport(await call<SetupReport>('/setup-check', { method: 'POST' }))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card data-testid="setup-check">
      <CardHeader>
        <CardTitle>Check my setup</CardTitle>
        <CardDescription>
          Checks the things that make links, sign-in and spending work: the database, key storage, the public address, Google redirects and the spend cap.
          It only reads; nothing is changed.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div>
          <Button type="button" onClick={() => void run()} disabled={busy}>
            {busy ? 'Checking…' : report ? 'Check again' : 'Check my setup'}
          </Button>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {report && (
          <div className="grid gap-2" aria-live="polite">
            <Notice tone={report.ok ? 'info' : report.fails ? 'error' : 'warn'}>
              {report.ok ? 'Everything looks fine.' : `${report.fails} to fix, ${report.warns} to look at.`}
            </Notice>
            <ul className="divide-y divide-line rounded-2xl border border-line">
              {report.results.map((r, i) => (
                <li key={`${r.name}-${i}`} className="px-4 py-3 text-sm" data-testid="setup-result" data-status={r.status}>
                  <p className="font-medium text-ink">
                    {r.name} <span className="ml-1 text-xs font-normal text-body">{LABEL[r.status]}</span>
                  </p>
                  {r.cause && <p className="mt-0.5 text-body">{r.cause}</p>}
                  {r.fix && r.status !== 'ok' && <p className="mt-0.5 text-ink">Fix: {r.fix}</p>}
                  {r.name === 'Photo reading' && r.fix && <PhotosOff onDone={() => void run()} />}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
