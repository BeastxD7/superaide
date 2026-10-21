import type { Metadata } from 'next'
import { cookies } from 'next/headers'
import type { ReactNode } from 'react'
import { DashboardScreen } from '@/app/(site)/dashboard/screen'

export const metadata: Metadata = { title: { default: 'Dashboard', template: '%s | Superaide' }, robots: { index: false } }

// The signed-in app. The sidebar remembers whether you collapsed it.
export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const defaultOpen = (await cookies()).get('sidebar_state')?.value !== 'false'
  return <DashboardScreen defaultOpen={defaultOpen}>{children}</DashboardScreen>
}
