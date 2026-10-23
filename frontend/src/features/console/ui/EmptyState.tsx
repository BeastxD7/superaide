import type { ReactNode } from 'react'

// Nothing here yet: say what it means and what to do next. The title is a heading (h2 by default).
export function EmptyState({ title, icon, children, level = 2 }: { title: string; icon?: ReactNode; children?: ReactNode; level?: 2 | 3 }) {
  const Heading = `h${level}` as 'h2' | 'h3'
  return (
    <div className="c-empty">
      {icon && <span className="c-empty-icon" aria-hidden="true">{icon}</span>}
      <Heading>{title}</Heading>
      {children && <p>{children}</p>}
    </div>
  )
}
