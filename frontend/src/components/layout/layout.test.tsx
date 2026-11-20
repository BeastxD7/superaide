import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { MasterDetail, PageContainer, PageHeader, ResponsiveGrid } from '@/components/layout'

describe('layout primitives', () => {
  it('PageContainer owns the scroller and the width of the page', () => {
    const out = html(<PageContainer width="wide">x</PageContainer>)
    expect(out).toContain('data-page-container')
    expect(out).toContain('overflow-y-auto')
    expect(out).toContain('max-w-[120rem]')
    expect(html(<PageContainer width="reading">x</PageContainer>)).toContain('max-w-3xl')
  })

  it('PageContainer puts an aside beside the content only when it is given one, labelled', () => {
    expect(html(<PageContainer>x</PageContainer>)).not.toContain('<aside')
    const out = html(<PageContainer aside={<p>facts</p>} asideLabel="About this person">x</PageContainer>)
    expect(out).toContain('aria-label="About this person"')
  })

  it('PageHeader keeps one h1, and a sticky one drops the description to stay small', () => {
    const plain = html(<PageHeader title="Reminders" description="Things to do" actions={<button>Add</button>} />)
    expect(plain.match(/<h1/g)).toHaveLength(1)
    expect(plain).toContain('Things to do')
    const sticky = html(<PageHeader sticky title="Reminders" description="Things to do" />)
    expect(sticky).toContain('sticky')
    expect(sticky).not.toContain('Things to do')
  })

  it('ResponsiveGrid sizes its columns by content width, not by breakpoints', () => {
    const out = html(<ResponsiveGrid as="ul" min="20rem" aria-label="Cards">x</ResponsiveGrid>)
    expect(out).toContain('<ul')
    expect(out).toContain('repeat(auto-fill, minmax(min(100%, 20rem), 1fr))')
    expect(html(<ResponsiveGrid fit>x</ResponsiveGrid>)).toContain('auto-fit')
  })

  it('MasterDetail shows the list alone on a narrow screen until something is chosen, then the detail alone', () => {
    const none = html(<MasterDetail list="L" listLabel="Requests" detail="D" detailLabel="Request" selected={false} empty="Pick one" />)
    expect(none).toContain('data-selected="false"')
    expect(none).toContain('Pick one')
    expect(none).not.toContain('>D<')
    const chosen = html(<MasterDetail list="L" listLabel="Requests" detail="D" detailLabel="Request" selected context="C" />)
    expect(chosen).toContain('>D<')
    expect(chosen).toContain('data-pane="context"')
    // the list pane hides below @3xl once an item is chosen
    expect(chosen).toMatch(/data-pane="list"[^>]*hidden @3xl\/md:block/)
  })
})
