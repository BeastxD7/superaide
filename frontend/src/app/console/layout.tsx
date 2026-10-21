import '@fontsource/atkinson-hyperlegible-next/400.css'
import '@fontsource/atkinson-hyperlegible-next/600.css'
import '@fontsource/atkinson-hyperlegible-next/700.css'
import '@fontsource/atkinson-hyperlegible-mono/400.css'
import '@/styles/console.css'
import type { Metadata } from 'next'
import type { ReactNode } from 'react'

export const metadata: Metadata = {
  title: 'Operator console | Superaide',
  robots: { index: false, follow: false },
  icons: { icon: '/favicon.svg' },
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
