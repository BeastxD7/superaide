'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Notice } from '@/components/site'
import { capabilityHref } from '@/features/dashboard/capabilities/registry'
import { SettingRow } from '@/features/dashboard/settings/SettingRow'
import { SettingsSection } from '@/features/dashboard/settings/SettingsSection'
import { call, type DriveStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// Whether Ava may propose new Docs and Sheets in the owner's Drive (Google asks for one more permission the first time).
export function DriveDrafts({ name }: { name: string }) {
  const [status, setStatus] = useState<DriveStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<DriveStatus>('/drive')
      .then((s) => live && setStatus(s))
      .catch((e) => live && setProblem(errorMessage(e)))
    return () => {
      live = false
    }
  }, [])

  async function set(enabled: boolean) {
    setBusy(true)
    setProblem(null)
    try {
      const out = await call<{ url?: string | null } & DriveStatus>('/drive/drafts', { method: 'POST', json: { enabled } })
      if (out.url) {
        window.location.assign(out.url) // Google's consent screen, then back to the Drive page
        return
      }
      setStatus(await call<DriveStatus>('/drive'))
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  if (!status.connected)
    return (
      <Notice tone="warn">
        Google Drive isn&apos;t connected yet, so there is nothing to change here.{' '}
        <Link href={capabilityHref('drive')} className="font-medium underline">
          Connect it on the Google Drive overview
        </Link>
        .
      </Notice>
    )
  return (
    <>
      {problem && (
        <div className="mb-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      <SettingsSection title="Drafts" description={`What ${name} may prepare in your Drive. Nothing is ever written without your OK.`}>
        <SettingRow
          title="Let Ava propose new Docs and Sheets"
          why={`${name} drafts a file and shows you the exact text. It is created only when you approve it, and she can add text only to Docs she made herself.`}
          example="You ask for a summary of the board minutes as a new Doc: Ava proposes it and waits for your yes."
          status={status.drafts ? 'On' : 'Off (default)'}
          changes="Google asks you to allow one more permission the first time (files Ava makes herself, drive.file). Reading stays read-only, and she can never delete, replace or share a file."
          audit={status.last_changed?.drafts ?? null}
          extra={
            status.drafts && !status.drafts_allowed ? (
              <p className="text-xs text-muted">Waiting for Google&apos;s permission: turn it off and on again to ask.</p>
            ) : undefined
          }
          control={({ labelId, describedBy }) => (
            <label className="flex min-h-11 items-center gap-2 text-sm text-body">
              <input
                type="checkbox"
                role="switch"
                aria-labelledby={labelId}
                aria-describedby={describedBy}
                className="size-5 accent-violet-600"
                checked={status.drafts}
                disabled={busy}
                onChange={(e) => void set(e.target.checked)}
              />
              {status.drafts ? 'On' : 'Off'}
            </label>
          )}
        />
      </SettingsSection>
    </>
  )
}
