import { describe, expect, it } from 'vitest'
import { describeReminderStep } from '@/lib/reminder-toast'

const ID = '0f6c1a2e-1111-4222-8333-444455556666'

describe('describeReminderStep', () => {
  it('turns the tool line into a short message and keeps the id for Undo', () => {
    const detail = `Reminder set (id ${ID}): Call Priya, tomorrow at 5:00 pm, arriving in the app.`
    expect(describeReminderStep('create_reminder', detail)).toEqual({ message: 'Reminder set: Call Priya, tomorrow at 5:00 pm', id: ID })
  })

  it('handles cancelled reminders and unknown tools', () => {
    expect(describeReminderStep('cancel_reminder', 'Reminder cancelled: Call Priya.')?.message).toBe('Reminder cancelled: Call Priya')
    expect(describeReminderStep('recall', 'Found 2 sources')).toBeNull()
  })

  it('falls back to the heading when the line has nothing more', () => {
    expect(describeReminderStep('update_reminder', '')).toEqual({ message: 'Reminder changed', id: null })
  })
})
