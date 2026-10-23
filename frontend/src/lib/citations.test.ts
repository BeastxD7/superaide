import { describe, expect, it } from 'vitest'
import type { SourceRef } from '@/lib/api'
import { citationNumber, markCitations, messageChannel, sourceLabel, sourceLink } from '@/lib/citations'

const src = (n: number, kind: SourceRef['kind'], uri: string, title = ''): SourceRef => ({ n, kind, uri, title, cited_text: '' })
const ID = '0f6c1a2e-1111-4222-8333-444455556666'

describe('markCitations', () => {
  const one = [src(1, 'memory', `ava://memory/${ID}`)]

  it('turns a known [n] into a cite link and leaves unknown numbers alone', () => {
    expect(markCitations('It is on Friday [1] or [2].', one)).toBe('It is on Friday[1](cite:1) or [2].')
  })

  it('shows a source once per paragraph, at its first use (the issue example repeated [1] three times)', () => {
    const text = 'Saved the reply. [1] It will wait. [1]\n\nYou prefer a new email. [1] I used the usual one. [1]'
    const out = markCitations(text, one)
    expect(out.match(/\(cite:1\)/g)).toHaveLength(2)
    expect(out).toBe('Saved the reply.[1](cite:1) It will wait.\n\nYou prefer a new email.[1](cite:1) I used the usual one.')
  })

  it('keeps marks for different sources in one paragraph and collapses back-to-back repeats', () => {
    const two = [...one, src(2, 'web', 'https://example.com')]
    expect(markCitations('A [1][1] B [2] C [1]', two)).toBe('A[1](cite:1) B[2](cite:2) C')
  })

  it('does nothing without sources, and never touches code or markdown links', () => {
    expect(markCitations('See [1]', undefined)).toBe('See [1]')
    expect(markCitations('See [1]', [])).toBe('See [1]')
    expect(markCitations('Run `a[1]` now [1]', one)).toBe('Run `a[1]` now[1](cite:1)')
    expect(markCitations('A [1](https://x.example) link', one)).toBe('A [1](https://x.example) link')
    expect(markCitations('```\nx[1]\n```', one)).toBe('```\nx[1]\n```')
  })
})

describe('citationNumber', () => {
  it('reads only cite: links with a positive whole number', () => {
    expect(citationNumber('cite:3')).toBe(3)
    expect(citationNumber('cite:0')).toBeNull()
    expect(citationNumber('cite:x')).toBeNull()
    expect(citationNumber('https://a.example')).toBeNull()
    expect(citationNumber(undefined)).toBeNull()
  })
})

describe('sourceLink', () => {
  it('sends a note to its Notebook entry', () => {
    expect(sourceLink(src(1, 'memory', `ava://memory/${ID}`))).toMatchObject({ href: `/dashboard/notebook#note-${ID}`, external: false })
  })
  it('sends an email to Gmail and a web source to the page', () => {
    expect(sourceLink(src(1, 'email', 'ava://mail/18c0ffee12ab'))?.href).toBe('https://mail.google.com/mail/u/0/#all/18c0ffee12ab')
    expect(sourceLink(src(1, 'web', 'https://example.com/a'))?.href).toBe('https://example.com/a')
  })
  it('gives no link for what has no page, and never builds one from odd ids', () => {
    expect(sourceLink(src(1, 'message', 'ava://msg/telegram/42'))).toBeNull()
    expect(sourceLink(src(1, 'calendar', 'ava://cal/abc'))).toBeNull()
    expect(sourceLink(src(1, 'memory', 'ava://chunk/' + ID))).toBeNull()
    expect(sourceLink(src(1, 'memory', 'ava://memory/../../x'))).toBeNull()
    expect(sourceLink(src(1, 'email', 'ava://mail/a/../b'))).toBeNull()
    expect(sourceLink(src(1, 'web', 'javascript:alert(1)'))).toBeNull()
  })
})

describe('labels', () => {
  it('names the source for screen readers', () => {
    expect(sourceLabel(src(1, 'memory', `ava://memory/${ID}`, 'Note · preference · from the app'))).toBe('Source 1: Note, preference, from the app')
    expect(sourceLabel(src(2, 'email', 'ava://mail/18c0ffee12ab', 'Squares'))).toBe('Source 2: Email, Squares')
  })
  it('names the channel of a message', () => {
    expect(messageChannel('ava://msg/telegram/42')).toBe('Telegram')
    expect(messageChannel('https://x')).toBeNull()
  })
})
