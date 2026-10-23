import { describe, expect, it } from 'vitest'
import { droppedBy, joinOlder, oldestSaved, PAGE } from '@/lib/chat-history'
import type { Message } from '@/lib/api'

const m = (id: string, at: string): Message => ({ id, from: 'you', text: id, at })

describe('chat history paging', () => {
  it('asks for a page the size the server defaults to', () => expect(PAGE).toBe(200))

  it('keeps the messages that fell out of the newest window', () => {
    const shown = [m('a', '2026-01-01T00:00:00Z'), m('b', '2026-01-02T00:00:00Z'), m('c', '2026-01-03T00:00:00Z')]
    const fresh = [m('b', '2026-01-02T00:00:00Z'), m('c', '2026-01-03T00:00:00Z'), m('d', '2026-01-04T00:00:00Z')]
    expect(droppedBy(shown, fresh).map((x) => x.id)).toEqual(['a'])
  })

  it('never treats something sent from this page and not yet saved as dropped', () => {
    const shown = [m('local-1', '2026-01-01T00:00:00Z')]
    expect(droppedBy(shown, [m('x', '2026-01-02T00:00:00Z')])).toEqual([])
  })

  it('has nothing to drop for an empty read', () => expect(droppedBy([m('a', '2026-01-01T00:00:00Z')], [])).toEqual([]))

  it('joins older ones without repeats', () => {
    const a = m('a', '2026-01-01T00:00:00Z')
    const b = m('b', '2026-01-02T00:00:00Z')
    expect(joinOlder([a], [a, b]).map((x) => x.id)).toEqual(['a', 'b'])
  })

  it('continues from the oldest saved message on screen', () => {
    const a = m('a', '2026-01-01T00:00:00Z')
    const b = m('b', '2026-01-02T00:00:00Z')
    expect(oldestSaved([a], [b])?.id).toBe('a')
    expect(oldestSaved([], [m('local-1', '2026-01-03T00:00:00Z'), b])?.id).toBe('b')
  })
})
