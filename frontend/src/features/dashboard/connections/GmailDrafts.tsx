'use client'

import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { SettingRow } from '@/features/dashboard/settings/SettingRow'
import { SettingsSection } from '@/features/dashboard/settings/SettingsSection'
import { call, type GmailStatus } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// Whether Ava may save reply drafts in the owner's Gmail (Google asks for one more permission the first time).
export function GmailDrafts({ name }: { name: string }) {
  const [status, setStatus] = useState<GmailStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    call<GmailStatus>('/gmail')
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
      const out = await call<{ url?: string | null } & GmailStatus>('/gmail/drafts', { method: 'POST', json: { enabled } })
      if (out.url) {
        window.location.assign(out.url) // Google's consent screen, then back to the Gmail page
        return
      }
      setStatus(await call<GmailStatus>('/gmail'))
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return problem ? <Notice tone="error">{problem}</Notice> : null
  return (
    <>
      {problem && (
        <div className="mb-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      <SettingsSection title="Drafts" description={`What ${name} may prepare in your Gmail. Nothing is ever sent without your OK.`}>
        <SettingRow
          title="Save reply drafts in my Gmail"
          why={`${name} writes a reply and leaves it in your Drafts folder for you to read and send yourself.`}
          example="Priya asks about Thursday: Ava saves a draft answer for you to check."
          status={status.drafts ? 'On' : 'Off (default)'}
          changes="Google asks you to allow one more permission (creating drafts) the first time. Reading stays read-only."
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
