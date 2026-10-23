import type { ButtonHTMLAttributes, ReactNode } from 'react'

// A row of controls (filters, actions). With a label it is a named group.
export function Toolbar({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="c-toolbar" role={label ? 'group' : undefined} aria-label={label}>
      {children}
    </div>
  )
}

// A filter chip: a toggle button, pressed when it is the active filter.
export function Chip({ pressed, ...rest }: { pressed: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className="c-chip" aria-pressed={pressed} {...rest} />
}
