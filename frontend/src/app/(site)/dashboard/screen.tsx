'use client'

import dynamic from 'next/dynamic'
import type { ReactNode } from 'react'

// Signed-in screens render in the browser: they're private, and the session
// state they need lives there.
const DashboardShell = dynamic(() => import('@/features/dashboard/Shell').then((m) => m.DashboardShell), {
  ssr: false,
  loading: () => <div className="min-h-dvh bg-sidebar" aria-busy="true" />,
})

export function DashboardScreen({ defaultOpen, children }: { defaultOpen: boolean; children: ReactNode }) {
  return <DashboardShell defaultOpen={defaultOpen}>{children}</DashboardShell>
}
