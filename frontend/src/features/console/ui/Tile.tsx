import type { ReactNode } from 'react'
import clsx from 'clsx'

// A quiet inner block for a card: a fact, a status, a step. Tiles in one Grid are equal in width and, per row, height.
export function Tile({ tone, className, children }: { tone?: 'ok' | 'warn' | 'danger'; className?: string; children: ReactNode }) {
  return <div className={clsx('c-tile', tone && `is-${tone}`, className)}>{children}</div>
}
