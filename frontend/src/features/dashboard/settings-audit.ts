import { formatWhen } from '@/lib/dates'

// "Last changed by you, Yesterday, 9:12 AM" under a setting, from the audit log. A setting nobody has changed says so.
export type Audit = { by: 'you' | 'Ava' | 'operator'; at: string } | null | undefined

export function lastChangedText(audit: Audit, now: Date = new Date()): string {
  if (!audit) return 'Never changed'
  const when = formatWhen(audit.at, now)
  return when ? `Last changed by ${audit.by}, ${when}` : `Last changed by ${audit.by}`
}
