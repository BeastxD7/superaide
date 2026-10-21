import '@fontsource-variable/geist'
import '@/styles/app.css'
import type { Metadata, Viewport } from 'next'
import type { ReactNode } from 'react'
import { Providers } from '@/app/(site)/providers'
import { currentAccount } from '@/lib/server'

export const metadata: Metadata = {
  title: { default: 'Superaide: AI personal assistants you hire', template: '%s | Superaide' },
  description:
    'Hire an AI personal assistant that fields the people who reach out when you are busy, keeps private things private, and interrupts you only for what matters.',
  icons: { icon: '/favicon.svg' },
}

export const viewport: Viewport = { themeColor: '#ffffff' }

// The person-facing site: marketplace, accounts, hiring, the introduction and the workspace.
export default async function SiteLayout({ children }: { children: ReactNode }) {
  const account = await currentAccount()
  return (
    <html lang="en">
      <body>
        <Providers initialAccount={account}>{children}</Providers>
      </body>
    </html>
  )
}
