import { describe, expect, it } from 'vitest'
import { computeWindow } from '@/lib/windowing'

describe('computeWindow', () => {
  it('renders only the rows near the viewport, with spacers that add up to the full height', () => {
    const w = computeWindow({ count: 100_000, rowHeight: 40, scrollTop: 40_000, viewport: 400, overscan: 5 })
    expect(w.start).toBe(995)
    expect(w.end).toBe(1015)
    expect(w.end - w.start).toBeLessThan(30)
    expect(w.padTop + (w.end - w.start) * 40 + w.padBottom).toBe(w.total)
  })
  it('starts at the top and never goes negative', () => {
    const w = computeWindow({ count: 50, rowHeight: 20, scrollTop: -300, viewport: 200, overscan: 4 })
    expect(w.start).toBe(0)
    expect(w.padTop).toBe(0)
  })
  it('clamps a scroll position past the end', () => {
    const w = computeWindow({ count: 10, rowHeight: 10, scrollTop: 9999, viewport: 30, overscan: 2 })
    expect(w.end).toBe(10)
    expect(w.padBottom).toBe(0)
  })
  it('handles an empty list and a bad row height', () => {
    expect(computeWindow({ count: 0, rowHeight: 40, scrollTop: 0, viewport: 400 }).total).toBe(0)
    expect(computeWindow({ count: 5, rowHeight: 0, scrollTop: 0, viewport: 400 }).end).toBe(0)
  })
})
