'use client'

import { ErrorPanel } from '@/components/ErrorPanel'

// A crash inside one dashboard screen. This sits inside the dashboard layout, so the sidebar stays.
export default function DashboardError({ error, retry }: { error: unknown; retry: () => void }) {
  return <ErrorPanel error={error} retry={retry} home={{ href: '/dashboard', label: 'Go to Home' }} signedIn />
}
