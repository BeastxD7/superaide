// One way to show a moment in time: within the last week it reads relative to now ("Today, 9:05 AM",
// "Yesterday, ...", "Wed, ..."); older (or further ahead than a week) it is the full date with the year.
// Always the browser's zone, which is also what a person's own clock shows.
const DAY = 86_400_000

export function formatWhen(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOfDay(d) - startOfDay(now)) / DAY)
  if (days === 0) return `Today, ${time}`
  if (days === -1) return `Yesterday, ${time}`
  if (days === 1) return `Tomorrow, ${time}`
  if (Math.abs(days) < 7) return `${d.toLocaleDateString(undefined, { weekday: 'short' })}, ${time}`
  return `${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}, ${time}`
}

// A date only (no time of day), always with the year.
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
