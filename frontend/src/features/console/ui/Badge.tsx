import type { ReactNode } from 'react'
import clsx from 'clsx'
import type { Tone } from '@/features/console/ui/Stat'

// A small status label. Colour is never the only signal: pass `dot` for a leading mark and keep the words.
export function Badge({ tone = 'neutral', dot, className, children }: { tone?: Tone; dot?: boolean; className?: string; children: ReactNode }) {
  return (
    <span className={clsx('c-badge', tone !== 'neutral' && `is-${tone}`, className)}>
      {dot && <span className="c-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}
