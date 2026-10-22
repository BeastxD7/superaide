import { describe, expect, it } from 'vitest'
import type { Account } from '@/lib/api'
import { homeFor, needsAccount, safeNext } from '@/lib/session'

describe('safeNext (the open-redirect guard)', () => {
  it.each(['/dashboard', '/agents/ava/hire', '/meet', '/a/some-handle'])('follows %s', (p) => expect(safeNext(p)).toBe(p))
  it.each([null, '', 'https://evil.example', '//evil.example', '/\\evil.example', 'javascript:alert(1)', '/%2f%2fevil', '/a?x=1', '/a#b', 'dashboard'])(
    'refuses %s',
    (p) => expect(safeNext(p as string | null)).toBeNull(),
  )
})

describe('homeFor', () => {
  const acct = (hire: Account['hire']) => ({ hire }) as Account
  it('sends people to where they belong', () => {
    expect(homeFor(null)).toBe('/')
    expect(homeFor(acct(null))).toBe('/')
    expect(homeFor(acct({ agent: 'ava', since: '', onboarding_done: false }))).toBe('/meet')
    expect(homeFor(acct({ agent: 'ava', since: '', onboarding_done: true }))).toBe('/dashboard')
  })
})

describe('needsAccount', () => {
  it('guards the signed-in pages only', () => {
    expect(needsAccount('/dashboard/chat')).toBe(true)
    expect(needsAccount('/agents/ava/hire')).toBe(true)
    expect(needsAccount('/agents/ava')).toBe(false)
    expect(needsAccount('/login')).toBe(false)
  })
})
