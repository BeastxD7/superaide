'use client'

import { useEffect, useRef } from 'react'
import { useReminders } from '@/features/dashboard/reminders/useReminders'
import { call } from '@/lib/api'
import { announceRemindersChanged } from '@/lib/reminders-sync'
import { toast } from '@/lib/toast'

// While the app is open, a reminder that falls due shows a toast with Snooze and Done. Only reminders that fall
// due after the page opened are announced (ones already overdue on arrival are counted by the pill, not shouted).
export function ReminderWatcher() {
  const { data, reload } = useReminders()
  const openedAt = useRef(0)
  const told = useRef(new Set<string>())
  const latest = useRef(data)
  useEffect(() => {
    latest.current = data
  })

  useEffect(() => {
    openedAt.current = Date.now()
  }, [])

  useEffect(() => {
    const check = () => {
      const now = Date.now()
      for (const r of latest.current?.reminders ?? []) {
        const due = new Date(r.due_at).getTime()
        if (r.status !== 'open' || told.current.has(r.id) || due > now || due < openedAt.current) continue
        told.current.add(r.id)
        const act = (path: string, json?: object) => () =>
          call(path, { method: 'POST', json }).then(
            () => announceRemindersChanged(),
            () => void reload(),
          )
        toast({
          message: `Reminder: ${r.text}`,
          ms: 20_000,
          actions: [
            { label: 'Snooze 1 hour', onClick: act(`/reminders/${r.id}/snooze`, { minutes: 60 }) },
            { label: 'Done', onClick: act(`/reminders/${r.id}/done`) },
          ],
        })
      }
    }
    check()
    const t = window.setInterval(check, 5_000)
    return () => window.clearInterval(t)
  }, [reload])

  return null
}
