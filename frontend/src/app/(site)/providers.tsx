'use client'

import { MotionConfig } from 'motion/react'
import type { ReactNode } from 'react'
import type { Account } from '@/lib/api'
import { SessionProvider } from '@/lib/session-context'

export function Providers({ initialAccount, children }: { initialAccount: Account | null; children: ReactNode }) {
  return (
    <SessionProvider initialAccount={initialAccount}>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </SessionProvider>
  )
}
