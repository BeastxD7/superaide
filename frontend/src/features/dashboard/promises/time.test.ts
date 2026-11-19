import { describe, expect, it } from 'vitest'
import { instant, localValue } from '@/features/dashboard/promises/time'

describe('promise times', () => {
  it('turns the wall clock of a datetime-local input into an instant with a zone, and back', () => {
    const local = '2026-11-20T15:08'
    const iso = instant(local)
    expect(iso).toMatch(/Z$/)
    expect(localValue(iso)).toBe(local)
  })

  it('pads single digits', () => {
    expect(localValue(new Date(2026, 0, 5, 7, 3).toISOString())).toBe('2026-01-05T07:03')
  })
})
