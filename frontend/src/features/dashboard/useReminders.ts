'use client'

import { useMemo } from 'react'
import type { Reminder } from '@/lib/api'
import { useRemindersChanged } from '@/lib/reminders-sync'
import { useResource } from '@/lib/use-resource'

// How often an open page asks again, so a reminder set from Telegram or another tab shows up without a reload.
export const REMINDERS_POLL_MS = 20_000

// The calendar day of an instant in a time zone, as YYYY-MM-DD (which sorts as text).
function dayIn(timezone: string, at: Date): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(at)
  } catch {
    return at.toISOString().slice(0, 10) // an unknown zone name: fall back to UTC rather than show nothing
  }
}

// What the pill counts: reminders still open that are due today or already overdue, by the owner's own clock.
// Done and cancelled ones never count; later days do not.
export function dueNow(reminders: Reminder[], timezone: string, now: Date = new Date()): Reminder[] {
  const today = dayIn(timezone, now)
  return reminders.filter((r) => r.status === 'open' && dayIn(timezone, new Date(r.due_at)) <= today)
}

// The reminders list, kept fresh like the approvals one: a timer, a change announced elsewhere in the app, the
// tab coming back. `due` is the number behind the pill.
export function useReminders() {
  const { data, error, reload } = useResource<{ timezone: string; reminders: Reminder[] }>('/reminders', {
    poll: REMINDERS_POLL_MS,
  })
  useRemindersChanged(reload)
  const due = useMemo(() => (data ? dueNow(data.reminders, data.timezone) : []), [data])
  return { data, error, reload, due }
}
