import '@fontsource-variable/geist'
import '@/styles/app.css'
import '@/styles/console.css'
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

// Rendered per request so Next can stamp this request's CSP nonce on its scripts (src/proxy.ts).
export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Operator console | Superaide',
  robots: { index: false, follow: false },
}

// The console has its own root layout (a full page load from the site, no session providers) but shares the
// site's design tokens and sidebar components (app.css), so it reads as one product. console.css sits in a
// CSS layer, so Tailwind utilities on the shared components always win over it.
export default function ConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
