import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { VisitorConversationView } from '@/lib/api'

const state: { conversations: VisitorConversationView[] } = { conversations: [] }
vi.mock('@/features/dashboard/data', () => ({
  useAppData: () => ({
    data: { conversations: state.conversations, handling: 0, idle_minutes: 150 },
    error: null,
    reload: async () => {},
  }),
}))

import { resumesIn } from '@/features/reception/resumes-in'
import { TakeoverSection } from '@/features/reception/TakeoverSection'

const c = (over: Partial<VisitorConversationView>): VisitorConversationView => ({
  context_id: 'c1', provider: 'telegram', name: 'Meera', last_at: '', takeover: null, messages: [], ...over,
})

// The owner-is-handling-this indicator and the hand-back button appear only while a takeover is active (#493).
describe('TakeoverSection', () => {
  it('shows nothing without conversations', () => {
    state.conversations = []
    expect(html(<TakeoverSection agentName="Ava" />)).toBe('')
  })

  it('offers to take over a conversation Ava is handling, with no indicator and no hand-back', () => {
    state.conversations = [c({})]
    const out = html(<TakeoverSection agentName="Ava" />)
    expect(out).toContain('Send and take over')
    expect(out).not.toContain('You’re handling this')
    expect(out).not.toContain('Give control back to Ava')
  })

  it('shows the indicator and the hand-back button while the owner is handling it', () => {
    state.conversations = [c({ takeover: { started_at: '', last_owner_message_at: '', resumes_at: new Date(Date.now() + 90 * 60000).toISOString() } })]
    const out = html(<TakeoverSection agentName="Ava" />)
    expect(out).toContain('You’re handling this. Ava isn’t replying here.')
    expect(out).toContain('Give control back to Ava')
  })

  it('shows what they wrote quoted and escaped while the owner handles it', () => {
    const takeover = { started_at: '', last_owner_message_at: '', resumes_at: new Date(Date.now() + 60000).toISOString() }
    state.conversations = [
      c({ takeover, messages: [{ from: 'visitor', text: '<img src=x onerror=alert(1)> call me', at: '1' }, { from: 'owner', text: 'On it', at: '2' }] }),
    ]
    const out = html(<TakeoverSection agentName="Ava" />)
    expect(out).toContain('call me”')
    expect(out).not.toMatch(/<img/i)
    expect(out).toContain('You: ')
  })

  it('escapes markup in the visitor’s name', () => {
    state.conversations = [c({ name: '<img src=x onerror=alert(1)>' })]
    expect(html(<TakeoverSection agentName="Ava" />)).not.toMatch(/<img/i)
  })

  it('says when she resumes in plain words', () => {
    const now = Date.parse('2026-11-19T10:00:00Z')
    expect(resumesIn('2026-11-19T10:00:20Z', now)).toBe('any moment now')
    expect(resumesIn('2026-11-19T10:45:00Z', now)).toBe('in about 45 minutes')
    expect(resumesIn('2026-11-19T12:30:00Z', now)).toBe('in about 2.5 hours')
  })
})
