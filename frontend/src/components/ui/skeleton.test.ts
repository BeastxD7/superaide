import { createElement as h } from 'react'
import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ListFooter } from '@/components/ui/list-footer'
import { SkeletonCard, SkeletonLine, SkeletonList, SkeletonRow, SkeletonTableRows } from '@/components/ui/skeleton'

const noop = () => {}
const footer = (p: Partial<Parameters<typeof ListFooter>[0]> = {}) =>
  html(h(ListFooter, { hasMore: false, loading: false, error: null, onLoadMore: noop, onRetry: noop, ...p }))

describe('skeleton primitives keep the size of the content they stand in for', () => {
  it('a row is exactly the given height, so the list does not jump when data arrives', () => {
    expect(html(h(SkeletonRow, { height: 72 }))).toContain('height:72px')
    expect(html(h(SkeletonCard, { height: 140 }))).toContain('height:140px')
    expect(html(h(SkeletonLine, { width: '60%', height: 20 }))).toMatch(/width:60%;?height:20px|height:20px;?width:60%/)
  })
  it('table rows render the requested rows by columns at the real row height', () => {
    const out = html(h('table', null, h('tbody', null, h(SkeletonTableRows, { rows: 3, columns: 4, rowHeight: 48 }))))
    expect(out.match(/<tr /g)).toHaveLength(3)
    expect(out.match(/<td/g)).toHaveLength(12)
    expect(out).toContain('height:48px')
  })
  it('the shimmer stops under reduced motion and the placeholder is hidden from screen readers', () => {
    const out = html(h(SkeletonLine, {}))
    expect(out).toContain('motion-reduce:animate-none')
    expect(out).toContain('aria-hidden="true"')
  })
  it('a skeleton list announces itself once', () => {
    const out = html(h(SkeletonList, { count: 4, what: 'your notebook' }))
    expect(out).toContain('role="status"')
    expect(out).toContain('aria-busy="true"')
    expect(out).toContain('Loading your notebook')
    expect(out.match(/data-skeleton="row"/g)).toHaveLength(4)
  })
})

describe('ListFooter', () => {
  it('offers Load more while there is more', () => {
    expect(footer({ hasMore: true })).toContain('Load more')
  })
  it('disables the button and says so while a page loads', () => {
    const out = footer({ hasMore: true, loading: true })
    expect(out).toContain('disabled')
    expect(out).toContain('Loading...')
  })
  it('says End of list at the end, and nothing at all for an empty list', () => {
    expect(footer()).toContain('End of list')
    expect(footer({ empty: true })).toBe('')
  })
  it('shows the error with a Retry button instead of Load more', () => {
    const out = footer({ hasMore: true, error: 'The server is busy.' })
    expect(out).toContain('role="alert"')
    expect(out).toContain('The server is busy.')
    expect(out).toContain('Retry')
    expect(out).not.toContain('Load more')
  })
})
