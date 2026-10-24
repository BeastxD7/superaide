import { describe, expect, it } from 'vitest'
import { lastChangedText } from '@/features/dashboard/settings-audit'

const now = new Date(2026, 9, 24, 12, 0)

describe('lastChangedText', () => {
  it('says so when nothing was ever changed', () => {
    expect(lastChangedText(null)).toBe('Never changed')
    expect(lastChangedText(undefined)).toBe('Never changed')
  })
  it('names who changed it and when, relative within a week', () => {
    expect(lastChangedText({ by: 'you', at: new Date(2026, 9, 24, 9, 12).toISOString() }, now)).toMatch(/^Last changed by you, Today, /)
    expect(lastChangedText({ by: 'operator', at: new Date(2026, 9, 23, 9, 12).toISOString() }, now)).toMatch(/^Last changed by operator, Yesterday, /)
  })
  it('keeps the year for an older change', () => {
    expect(lastChangedText({ by: 'Ava', at: new Date(2026, 7, 2, 9, 12).toISOString() }, now)).toContain('2026')
  })
  it('does not print an empty time for a date it cannot read', () => {
    expect(lastChangedText({ by: 'you', at: 'nonsense' }, now)).toBe('Last changed by you')
  })
})
