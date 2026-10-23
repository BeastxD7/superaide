import type { ReactNode } from 'react'
import clsx from 'clsx'

// An inline message. Errors use role="alert" so they are read out; info and success are polite.
export function Notice({ tone = 'info', children, className }: { tone?: 'info' | 'ok' | 'warn' | 'danger'; children: ReactNode; className?: string }) {
  return (
    <div className={clsx('c-notice', tone !== 'info' && `is-${tone}`, className)} role={tone === 'danger' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}
