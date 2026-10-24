import type { ReactNode } from 'react'

// One group of settings, the same everywhere in the dashboard: a titled card holding SettingRows.
// (docs/design/dashboard-ia.md, section 4.)
export function SettingsSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="mb-4 overflow-hidden rounded-2xl border border-line bg-white" aria-label={title}>
      <header className="border-b border-line bg-mist px-4 py-3">
        <h2 className="text-base font-medium text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-body">{description}</p>}
      </header>
      <ul className="divide-y divide-line">{children}</ul>
    </section>
  )
}
