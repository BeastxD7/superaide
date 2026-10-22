'use client'

import { MotionConfig } from 'motion/react'
import type { ReactNode } from 'react'
import type { Account } from '@/lib/api'
import { PwaSetup } from '@/features/pwa/register'
import { PWA_ENABLED } from '@/lib/flags'
import { SessionProvider } from '@/lib/session-provider'

type Props = { initialAccount: Account | null; hasSession: boolean; children: ReactNode }

export function Providers({ initialAccount, hasSession, children }: Props) {
  return (
    <SessionProvider initialAccount={initialAccount} hasSession={hasSession}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
      {PWA_ENABLED && <PwaSetup signedIn={hasSession} />}
    </SessionProvider>
  )
}
