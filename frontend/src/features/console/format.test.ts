import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuditRow } from '@/features/console/api'
import { ago, describeAudit, usd } from '@/features/console/format'

const row = (action: string, detail: Record<string, unknown> = {}, actor = 'owner'): AuditRow => ({
  id: '1',
  actor,
  action,
  detail,
  at: '2026-01-01T00:00:00Z',
})

describe('ago', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-06-15T12:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())
  const before = (secs: number) => new Date(Date.now() - secs * 1000).toISOString()

  it('says never for no time', () => expect(ago(null)).toBe('never'))
  it('says just now under a minute, and for a time in the future', () => {
    expect(ago(before(5))).toBe('just now')
    expect(ago(before(-3600))).toBe('just now')
  })
  it('counts minutes and hours', () => {
    expect(ago(before(60))).toBe('1 min ago')
    expect(ago(before(59 * 60))).toBe('59 min ago')
    expect(ago(before(3600))).toBe('1 h ago')
    expect(ago(before(23 * 3600))).toBe('23 h ago')
  })
  it('falls back to a date after a day', () => expect(ago(before(2 * 86400))).toMatch(/13|Jun/))
})

describe('usd', () => {
  it('pads to four places by default', () => expect(usd(0.5)).toBe('US$0.5000'))
  it('takes a precision', () => expect(usd(1.23456, 2)).toBe('US$1.23'))
})

describe('describeAudit', () => {
  it('categorises identity, refusal, model, memory and control rows', () => {
    expect(describeAudit(row('new_identity', { provider: 'slack' }))).toEqual({
      kind: 'identity',
      text: 'A new slack account wrote in for the first time.',
    })
    expect(describeAudit(row('link_code_wrong', { attempts: 2 })).kind).toBe('refusal')
    expect(describeAudit(row('llm_call_settled', { purpose: 'chat', model: 'm', cost_usd: 0.0123456 })).text).toBe(
      'Model call for chat on m: US$0.01235.',
    )
    expect(describeAudit(row('remembered')).kind).toBe('memory')
    expect(describeAudit(row('kill_switch', { on: true }, 'op')).text).toBe('Paused by op.')
    expect(describeAudit(row('kill_switch', { on: false }, 'op')).text).toBe('Resumed by op.')
  })
  it('tells a pause from a spend-cap block', () => {
    expect(describeAudit(row('llm_call_refused', { reason: 'frozen' })).text).toMatch(/paused/)
    expect(describeAudit(row('llm_call_refused', { reason: 'cap' })).text).toMatch(/spend cap/)
  })
  it('survives missing detail fields without printing undefined or null', () => {
    for (const a of ['claim_seen', 'person_bootstrapped', 'action_refused', 'prediction', 'rejected', 'llm_call_failed']) {
      expect(describeAudit(row(a, { claimed: null })).text).not.toMatch(/undefined|null|NaN/)
    }
  })
  it('names what an operator opened and keeps their reason', () => {
    expect(describeAudit(row('operator_access', { path: '/api/memories', reason: 'support case' })).text).toBe(
      'You opened Memories — reason: support case',
    )
    expect(describeAudit(row('operator_access', { path: '/api/memories/page', reason: 'support case' })).text).toBe(
      'You opened Memories — reason: support case',
    ) // the paged route is the same screen
    expect(describeAudit(row('operator_access', { path: '/api/people/unlinked/page', reason: 'r' })).text).toContain('opened People')
    expect(describeAudit(row('operator_access', { path: '/api/identities/x/unlink', reason: 'r' })).text).toContain('unlinked an account')
    expect(describeAudit(row('operator_access', { path: '', reason: 'r' })).text).toContain('a private page')
  })
  it('turns an unknown action into plain words', () => {
    expect(describeAudit(row('some_new_thing'))).toEqual({ kind: 'control', text: 'some new thing' })
  })
})
