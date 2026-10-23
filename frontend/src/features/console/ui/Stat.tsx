import type { CSSProperties, ReactNode } from 'react'
import clsx from 'clsx'

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'accent'

// A number with a small label. Every tile has the same minimum height, so rows stay level.
export function Stat({ label, value, tone = 'neutral' }: { label: ReactNode; value: ReactNode; tone?: Tone }) {
  return (
    <div className={clsx('c-stat', (tone === 'warn' || tone === 'danger') && `is-${tone}`)}>
      <dt className="c-stat-label">{label}</dt>
      <dd className="c-stat-value">{value}</dd>
    </div>
  )
}

// A row of equal tiles. Use a count that fills the rows (2, 3 or 4 per row) so no tile is left alone.
export function Stats({ cols = 3, label, children }: { cols?: 2 | 3 | 4; label: string; children: ReactNode }) {
  return (
    <dl className="c-stats" aria-label={label} style={{ '--cols': cols } as CSSProperties}>
      {children}
    </dl>
  )
}
