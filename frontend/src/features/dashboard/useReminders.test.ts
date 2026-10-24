import { describe, expect, it } from 'vitest'
import type { Reminder } from '@/lib/api'
import { dueNow } from '@/features/dashboard/useReminders'

const r = (id: string, due_at: string, status: Reminder['status'] = 'open'): Reminder => ({
  id,
  kind: 'reminder',
  text: id,
  due_at,
  due_text: '',
  status,
  delivered_at: null,
  source: 'chat',
})

describe('dueNow (the number on the Reminders pill)', () => {
  // 2026-10-24 22:30 in New York is already 2026-10-25 02:30 UTC.
  const now = new Date('2026-10-25T02:30:00Z')

  it('counts what is due today or overdue by the owner\'s own clock, not UTC', () => {
    const list = [
      r('earlier-today', '2026-10-24T14:00:00Z'), // 10 am New York, today
      r('tonight', '2026-10-25T03:30:00Z'), // 11:30 pm New York, still today there
      r('tomorrow', '2026-10-25T14:00:00Z'), // 10 am the 25th New York
      r('overdue', '2026-10-20T14:00:00Z'),
    ]
    expect(dueNow(list, 'America/New_York', now).map((x) => x.id)).toEqual(['earlier-today', 'tonight', 'overdue'])
    // In UTC the same instant is already the 25th, so "tomorrow" (14:00 on the 25th) is due there.
    expect(dueNow(list, 'UTC', now).map((x) => x.id)).toEqual(['earlier-today', 'tonight', 'tomorrow', 'overdue'])
  })

  it('never counts done or cancelled reminders', () => {
    const list = [r('a', '2026-10-24T10:00:00Z', 'done'), r('b', '2026-10-24T10:00:00Z', 'cancelled'), r('c', '2026-10-24T10:00:00Z')]
    expect(dueNow(list, 'UTC', now).map((x) => x.id)).toEqual(['c'])
  })

  it('is empty when nothing is due, and survives an unknown time zone', () => {
    expect(dueNow([r('later', '2026-12-01T10:00:00Z')], 'UTC', now)).toEqual([])
    expect(dueNow([r('x', '2026-10-24T10:00:00Z')], 'Not/AZone', now)).toHaveLength(1)
  })
})
