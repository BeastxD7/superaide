import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { VisitorQuestionView } from '@/lib/api'

const state: { questions: VisitorQuestionView[] } = { questions: [] }
vi.mock('@/features/dashboard/data', () => ({
  useAppData: () => ({ data: { questions: state.questions, open: state.questions.length }, error: null, reload: async () => {} }),
}))

import { QuestionsSection } from '@/features/reception/QuestionsSection'

const q = (over: Partial<VisitorQuestionView>): VisitorQuestionView => ({
  id: '1', provider: 'slack', name: 'Dev', question: 'What floor is the office?', state: 'asked', answer: '', delivery: '', created_at: '',
  ...over,
})

// What a visitor typed is shown quoted and as text (#494); nothing is offered for a question already handled.
describe('QuestionsSection', () => {
  it('shows nothing when no question waits', () => {
    state.questions = [q({ state: 'answered' })]
    expect(html(<QuestionsSection agentName="Ava" />)).toBe('')
  })

  it('shows a waiting question quoted, with who says so and where, and the two choices', () => {
    state.questions = [q({})]
    const out = html(<QuestionsSection agentName="Ava" />)
    expect(out).toContain('Questions for you (1)')
    expect(out).toContain('“What floor is the office?”')
    expect(out).toContain('Dev (says so; not verified) on Slack')
    expect(out).toContain('Send my answer')
    expect(out).toContain('Don’t answer')
  })

  it('escapes markup in the visitor’s words', () => {
    state.questions = [q({ question: '<img src=x onerror=alert(1)>' })]
    const out = html(<QuestionsSection agentName="Ava" />)
    expect(out).not.toMatch(/<img/i)
    expect(out).toContain('&lt;img')
  })
})
