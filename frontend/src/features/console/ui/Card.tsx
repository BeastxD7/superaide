import type { CSSProperties, ElementType, ReactNode } from 'react'
import clsx from 'clsx'

type CardProps = {
  title?: ReactNode
  /** The id the heading gets, so the section can name itself (aria-labelledby). */
  titleId?: string
  description?: ReactNode
  actions?: ReactNode
  tinted?: boolean
  className?: string
  children?: ReactNode
}

// A 28px panel. With a title it is a labelled region (section + h2).
export function Card({ title, titleId, description, actions, tinted, className, children }: CardProps) {
  const Tag: ElementType = title ? 'section' : 'div'
  return (
    <Tag className={clsx('c-card', tinted && 'c-card-tinted', className)} aria-labelledby={title ? titleId : undefined}>
      {(title || actions || description) && (
        <div className="c-card-head">
          {title && <h2 id={titleId}>{title}</h2>}
          {description && <p>{description}</p>}
          {actions}
        </div>
      )}
      {children}
    </Tag>
  )
}

// Equal columns with one gap. Collapses to a single column on phones (see console.css).
export function Grid({ cols = 2, className, children }: { cols?: 2 | 3 | 4; className?: string; children: ReactNode }) {
  return (
    <div className={clsx('c-grid', className)} style={{ '--cols': cols } as CSSProperties}>
      {children}
    </div>
  )
}
