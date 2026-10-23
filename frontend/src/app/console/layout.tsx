import '@fontsource-variable/geist'
import '@/styles/console.css'
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

// Rendered per request so Next can stamp this request's CSP nonce on its scripts (src/proxy.ts).
export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Operator console | Superaide',
  robots: { index: false, follow: false },
}

// The operator console has its own look, so it has its own root layout:
// moving between it and the site is a full page load, and the styles never mix.
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
