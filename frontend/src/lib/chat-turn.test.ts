import { describe, expect, it } from 'vitest'
import type { LiveTurn } from '@/components/Conversation'
import type { ChatEvent } from '@/lib/api'
import { apply } from '@/lib/chat-turn'

const empty: LiveTurn = { steps: [], reasoning: '', text: '', status: '' }
const run = (events: ChatEvent[], from: LiveTurn = empty) => events.reduce(apply, from)
const tool = (state: 'running' | 'done' | 'error', label = 'Searching notes'): ChatEvent => ({
  event: 'tool',
  data: { id: 't1', name: 'search', state, label, detail: '' },
})

describe('apply (the chat event reducer)', () => {
  it('appends text deltas and says it is writing', () => {
    const t = run([
      { event: 'text', data: { delta: 'Hel' } },
      { event: 'text', data: { delta: 'lo' } },
    ])
    expect(t.text).toBe('Hello')
    expect(t.status).toBe('Writing')
  })
  it('collects reasoning separately from the answer', () => {
    const t = run([
      { event: 'reasoning', data: { delta: 'a' } },
      { event: 'reasoning', data: { delta: 'b' } },
    ])
    expect(t.reasoning).toBe('ab')
    expect(t.text).toBe('')
  })
  it('turns what she said before a tool into a step, not the answer', () => {
    const t = run([
      { event: 'text', data: { delta: 'Let me check' } },
      { event: 'narration', data: { text: 'Let me check' } },
    ])
    expect(t.text).toBe('')
    expect(t.steps).toEqual([{ kind: 'say', label: 'Let me check' }])
  })
  it('updates one tool step in place as it goes running, then done', () => {
    const t = run([tool('running'), tool('done', 'Searched notes')])
    expect(t.steps).toHaveLength(1)
    expect(t.steps[0]).toMatchObject({ kind: 'tool', state: 'done', label: 'Searched notes' })
    expect(t.status).toBe('Thinking')
  })
  it('shows Working while a tool runs', () => expect(run([tool('running')]).status).toBe('Working'))
  it('clears the draft on retract so the rewrite streams in its place', () => {
    const t = run([{ event: 'text', data: { delta: 'bad draft' } }, { event: 'retract', data: {} }, { event: 'text', data: { delta: 'good' } }])
    expect(t.text).toBe('good')
  })
  it('ignores done and error events (the caller handles them) and never mutates its input', () => {
    const before = JSON.stringify(empty)
    expect(apply(empty, { event: 'done', data: { mode: 'x', reply: null, notes: [] } })).toBe(empty)
    expect(apply(empty, { event: 'error', data: { message: 'x' } })).toBe(empty)
    run([{ event: 'text', data: { delta: 'x' } }, tool('running')])
    expect(JSON.stringify(empty)).toBe(before)
  })
})
