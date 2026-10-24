'use client'

import { useId, type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { lastChangedText, type Audit } from '@/features/dashboard/settings-audit'

// What a control needs to be labelled and described by the row it sits in.
export type ControlIds = { labelId: string; describedBy: string }

// One setting: what it is, why, a real example, its status, the control, what changes, and who changed it last.
// The control is a render prop so it is always named by the title and described by the rest (accessible by construction).
// On a phone the control moves under the text (single column, 44px targets).
export function SettingRow({
  title,
  why,
  example,
  status,
  changes,
  audit,
  danger = false,
  control,
}: {
  title: string
  why: string
  example?: string
  /** A short badge: "On", "Off (default)", "3 of 5 on". */
  status?: string
  /** What happens if the owner turns it on (or changes it), in plain words. */
  changes?: string
  audit?: Audit
  /** A revoke or delete: the status badge is red-toned and the control should confirm. */
  danger?: boolean
  control: (ids: ControlIds) => ReactNode
}) {
  const base = useId()
  const labelId = `${base}-title`
  const describedBy = [`${base}-why`, example && `${base}-example`, changes && `${base}-changes`].filter(Boolean).join(' ')
  return (
    <li className="grid gap-3 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-6" data-testid="setting-row">
      <div className="min-w-0">
        <h3 id={labelId} className="flex flex-wrap items-center gap-2 text-[15px] font-medium text-ink">
          {title}
          {status && <Badge variant={danger ? 'destructive' : 'secondary'} className={danger ? '' : 'text-violet-700'}>{status}</Badge>}
        </h3>
        <p id={`${base}-why`} className="mt-0.5 text-sm text-body">{why}</p>
        {example && (
          <p id={`${base}-example`} className="mt-2 rounded-lg bg-mist px-2.5 py-1.5 text-[13px] text-muted">
            <span className="font-medium text-ink">For example:</span> {example}
          </p>
        )}
        {changes && (
          <p id={`${base}-changes`} className="mt-2 text-[13px] text-muted">
            <span className="font-medium text-ink">What changes:</span> {changes}
          </p>
        )}
        {audit !== undefined && <p className="mt-2 text-xs text-muted">{lastChangedText(audit)}</p>}
      </div>
      <div className="flex min-w-0 items-start sm:justify-end">{control({ labelId, describedBy })}</div>
    </li>
  )
}
