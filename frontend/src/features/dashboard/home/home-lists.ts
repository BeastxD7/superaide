import type { Reminder } from '@/lib/api'

// Home shows a few of each list. These say what is shown and how many more exist, so a card never reads as
// the whole list when it is not (#301).
export function firstOf<T>(items: T[], limit: number): { shown: T[]; more: number } {
  return { shown: items.slice(0, limit), more: Math.max(0, items.length - limit) }
}

// "Coming up" is what is still ahead. An open reminder that already went off (the delivery job leaves it open
// until the owner marks it done) is counted apart, never listed as upcoming (#295).
export function comingUp(reminders: Reminder[], limit: number) {
  const ahead = reminders.filter((r) => !r.delivered_at)
  return { ...firstOf(ahead, limit), sent: reminders.length - ahead.length }
}
