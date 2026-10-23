import type { ReactNode } from 'react'

// The one h1 of a page, with an optional line under it and actions on the right.
export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="c-head">
      <div className="c-head-text">
        <h1>{title}</h1>
        {description && <p className="lede">{description}</p>}
      </div>
      {actions && <div className="c-head-actions">{actions}</div>}
    </header>
  )
}
