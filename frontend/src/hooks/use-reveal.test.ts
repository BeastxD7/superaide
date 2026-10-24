// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Message } from '@/lib/api'
import { useReveal } from '@/hooks/use-reveal'

// Ava's lines arrive one at a time with a moment of "typing"; your own lines and replays appear at once.
let reduced = false
vi.mock('motion/react', () => ({ useReducedMotion: () => reduced }))

const at = '2026-10-22T10:00:00Z'
const you = (id: string, text = 'hi'): Message => ({ id, from: 'you', text, at })
const ava = (id: string, text = 'Hello there'): Message => ({ id, from: 'agent', text, at })

beforeEach(() => {
  reduced = false
  vi.useFakeTimers()
})
afterEach(() => vi.useRealTimers())

const ids = (r: { current: { visible: Message[] } }) => r.current.visible.map((m) => m.id)

describe('useReveal', () => {
  it('shows an existing conversation at once when it is not brand new', () => {
    const { result } = renderHook(() => useReveal([ava('a'), you('b'), ava('c')], false, false))
    expect(ids(result)).toEqual(['a', 'b', 'c'])
    expect(result.current.typing).toBe(false)
    expect(result.current.settled).toBe(true)
  })

  it('shows nothing and is not settled while there are no messages', () => {
    const { result } = renderHook(() => useReveal([], false, true))
    expect(result.current.visible).toEqual([])
    expect(result.current.settled).toBe(false)
  })

  it('reveals a new conversation one agent line at a time, typing before each', () => {
    const { result } = renderHook(() => useReveal([ava('a', 'Hello there'), ava('b', 'Second line')], false, true))
    expect(ids(result)).toEqual([])
    expect(result.current.typing).toBe(true)
    act(() => void vi.advanceTimersByTime(649)) // the shortest pause is 650 ms
    expect(ids(result)).toEqual([])
    act(() => void vi.advanceTimersByTime(1))
    expect(ids(result)).toEqual(['a'])
    expect(result.current.typing).toBe(true)
    act(() => void vi.advanceTimersByTime(2000)) // the longest pause is 1900 ms
    expect(ids(result)).toEqual(['a', 'b'])
    expect(result.current.typing).toBe(false)
    expect(result.current.settled).toBe(true)
  })

  it('shows your own lines at once, without a pause', () => {
    const { result } = renderHook(() => useReveal([you('a'), you('b')], false, true))
    expect(ids(result)).toEqual(['a', 'b'])
  })

  it('skips the pause when replies were already watched arriving (instant)', () => {
    const { result } = renderHook(() => useReveal([ava('a'), ava('b')], false, true, true))
    expect(ids(result)).toEqual(['a', 'b'])
  })

  it('skips the pause for people who prefer reduced motion', () => {
    reduced = true
    const { result } = renderHook(() => useReveal([ava('a'), ava('b')], false, true))
    expect(ids(result)).toEqual(['a', 'b'])
  })

  it('keeps typing while a reply is pending, even when everything is shown', () => {
    const { result } = renderHook(() => useReveal([you('a')], true, false))
    expect(ids(result)).toEqual(['a'])
    expect(result.current.typing).toBe(true)
    expect(result.current.settled).toBe(false)
  })

  it('reveals a line that arrives later with its pause, and clears the timer on unmount', () => {
    const first = [ava('a')]
    const { result, rerender, unmount } = renderHook(({ m }) => useReveal(m, false, false), { initialProps: { m: first } })
    expect(ids(result)).toEqual(['a'])
    rerender({ m: [...first, ava('b', 'x'.repeat(200))] })
    expect(ids(result)).toEqual(['a'])
    expect(result.current.typing).toBe(true)
    act(() => void vi.advanceTimersByTime(1900))
    expect(ids(result)).toEqual(['a', 'b'])
    rerender({ m: [...first, ava('b'), ava('c')] })
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
