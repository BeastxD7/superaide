'use client'

import { ErrorPanel } from '@/components/ErrorPanel'

// A crash on any public page (sign up, log in, the marketplace…). The dashboard has its own.
export default function SiteError({ error, retry }: { error: unknown; retry: () => void }) {
  return <ErrorPanel error={error} retry={retry} fullPage home={{ href: '/', label: 'Go to the home page' }} />
}
