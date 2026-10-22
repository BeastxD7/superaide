import { describe, expect, it } from 'vitest'
import { formatDay, formatWhen } from '@/lib/dates'

const now = new Date(2026, 9, 23, 12, 0)

describe('formatWhen', () => {
  it('is empty for nothing or nonsense', () => {
    expect(formatWhen(null)).toBe('')
    expect(formatWhen('not a date')).toBe('')
  })
  it('is relative within a week', () => {
    expect(formatWhen(new Date(2026, 9, 23, 9, 5).toISOString(), now)).toMatch(/^Today, /)
    expect(formatWhen(new Date(2026, 9, 22, 9, 5).toISOString(), now)).toMatch(/^Yesterday, /)
  })
  it('keeps the year for anything older, such as last month', () => {
    expect(formatWhen(new Date(2026, 8, 9, 9, 5).toISOString(), now)).toContain('2026')
  })
})

describe('formatDay', () => {
  it('always has the year', () => expect(formatDay(new Date(2026, 8, 9).toISOString())).toContain('2026'))
})
