import { describe, expect, it } from 'vitest'
import { comingUp, firstOf } from '@/features/dashboard/home-lists'
import type { Reminder } from '@/lib/api'

const r = (text: string, delivered_at: string | null = null) => ({ id: text, text, delivered_at }) as Reminder

describe('firstOf', () => {
  it('says how many more there are', () => {
    expect(firstOf([1, 2, 3, 4, 5, 6, 7], 5)).toEqual({ shown: [1, 2, 3, 4, 5], more: 2 })
    expect(firstOf([1, 2], 5)).toEqual({ shown: [1, 2], more: 0 })
  })
})

describe('comingUp', () => {
  it('leaves out a reminder that already went off and counts it apart', () => {
    const out = comingUp([r('Call Bob', '2026-10-23T12:00:00Z'), r('Pay rent')], 3)
    expect(out.shown.map((x) => x.text)).toEqual(['Pay rent'])
    expect(out).toMatchObject({ more: 0, sent: 1 })
  })
  it('counts the ones past the limit', () => {
    const out = comingUp([r('a'), r('b'), r('c'), r('d'), r('e')], 3)
    expect(out.shown).toHaveLength(3)
    expect(out.more).toBe(2)
  })
})
