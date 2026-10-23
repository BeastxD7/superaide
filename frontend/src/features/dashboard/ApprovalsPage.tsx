'use client'

import { ApprovalsCard } from '@/features/dashboard/Approvals'
import { MandatesCard } from '@/features/dashboard/MandatesCard'
import { TwinCard } from '@/features/dashboard/TwinCard'
import { useSession } from '@/lib/session-context'

// Approvals on their own page (ADR 0011 section 2), for the phone's tab bar. The same card
// as on Home, with a line for when nothing is waiting.
export function ApprovalsPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Approvals</h1>
        <p className="mt-1 text-body">Things {name} was asked to do that need your yes first.</p>
        <ApprovalsCard
          name={name}
          showExpired
          empty={
            <p className="mt-6 rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
              Nothing is waiting for your OK.
            </p>
          }
        />
        <MandatesCard name={name} />
        <TwinCard name={name} />
      </div>
    </div>
  )
}
