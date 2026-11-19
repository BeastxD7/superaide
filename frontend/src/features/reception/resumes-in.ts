export function resumesIn(resumesAt: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((new Date(resumesAt).getTime() - now) / 60000))
  if (minutes < 1) return 'any moment now'
  if (minutes < 60) return `in about ${minutes} minute${minutes === 1 ? '' : 's'}`
  const hours = Math.round(minutes / 6) / 10
  return `in about ${hours} hours`
}
