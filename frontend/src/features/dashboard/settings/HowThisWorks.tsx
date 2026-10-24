import { ShieldCheck } from 'lucide-react'

// The rules that are fixed on purpose, shown read-only next to the ones the owner can change (#433): safety rules
// that are decided in code (never a setting a message, a model or an email could flip). Plain sentences, no jargon.
export function HowThisWorks({ title = 'How this works', rules }: { title?: string; rules: string[] }) {
  if (!rules.length) return null
  return (
    <section className="mb-4 overflow-hidden rounded-2xl border border-line bg-white" aria-label={title}>
      <header className="flex items-center gap-2 border-b border-line bg-mist px-4 py-3">
        <ShieldCheck className="size-4 text-violet-600" aria-hidden />
        <h2 className="text-base font-medium text-ink">{title}</h2>
      </header>
      <p className="px-4 pt-3 text-sm text-body">These are fixed: they protect you, so nothing can switch them off, not even a message.</p>
      <ul className="list-disc space-y-1.5 px-8 py-3 text-sm text-body">
        {rules.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </section>
  )
}
