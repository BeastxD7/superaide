'use client'

import { MotionConfig } from 'motion/react'
import type { ReactNode } from 'react'
import type { Account } from '@/lib/api'
import { SessionProvider } from '@/lib/session-context'

type Props = { initialAccount: Account | null; hasSession: boolean; children: ReactNode }

export function Providers({ initialAccount, hasSession, children }: Props) {
  return (
    <SessionProvider initialAccount={initialAccount} hasSession={hasSession}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </SessionProvider>
  )
}
