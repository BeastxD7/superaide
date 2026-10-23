import { describe, expect, it, vi } from 'vitest'
import { PaginatedController, pageUrl, type PageResponse } from '@/lib/paginated-core'

type Row = { id: string }
const rows = (...ids: string[]): Row[] => ids.map((id) => ({ id }))
const page = (items: Row[], next: string | null): PageResponse<Row> => ({ items, next_cursor: next, has_more: next !== null })

function make(fetcher: (url: string, signal: AbortSignal) => Promise<PageResponse<Row> | Record<string, unknown>>, extra: object = {}) {
  return new PaginatedController<Row>({
    path: '/things',
    limit: 2,
    fetcher,
    getId: (r) => r.id,
    describeError: (e) => (e instanceof Error ? e.message : 'x'),
    ...extra,
  })
}

describe('pageUrl', () => {
  it('adds limit, cursor and non-empty filters', () => {
    expect(pageUrl('/a', 25, null, { q: 'x y', empty: '', none: null })).toBe('/a?q=x+y&limit=25')
    expect(pageUrl('/a?kind=b', 10, 'c.d', {})).toBe('/a?kind=b&limit=10&cursor=c.d')
  })
})

describe('PaginatedController', () => {
  it('loads the first page, then appends the next', async () => {
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a', 'b'), 'c1'))
      .mockResolvedValueOnce(page(rows('c'), null))
    const c = make(f)
    expect(c.getSnapshot().status).toBe('loading')
    await c.start()
    expect(c.getSnapshot()).toMatchObject({ status: 'ready', hasMore: true })
    await c.loadMore()
    expect(c.getSnapshot().items.map((r) => r.id)).toEqual(['a', 'b', 'c'])
    expect(c.getSnapshot().hasMore).toBe(false)
    expect(f.mock.calls[1][0]).toBe('/things?limit=2&cursor=c1')
    await c.loadMore() // the list ended: no request
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('asks the server once for a double click on Load more', async () => {
    let release: (p: PageResponse<Row>) => void = () => {}
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a'), 'c1'))
      .mockImplementationOnce(() => new Promise((r) => (release = r)))
    const c = make(f)
    await c.start()
    const one = c.loadMore()
    void c.loadMore()
    void c.loadMore()
    expect(c.getSnapshot().status).toBe('loading-more')
    release(page(rows('b'), null))
    await one
    expect(f).toHaveBeenCalledTimes(2)
  })

  it('never shows the same row twice when a page overlaps', async () => {
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a', 'b'), 'c1'))
      .mockResolvedValueOnce(page(rows('b', 'c'), null))
    const c = make(f)
    await c.start()
    await c.loadMore()
    expect(c.getSnapshot().items.map((r) => r.id)).toEqual(['a', 'b', 'c'])
  })

  it('keeps the shown rows when loading more fails, and retry asks for the same page', async () => {
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a'), 'c1'))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(page(rows('b'), null))
    const c = make(f)
    await c.start()
    await c.loadMore()
    expect(c.getSnapshot()).toMatchObject({ status: 'error', failed: 'more', error: 'offline' })
    expect(c.getSnapshot().items).toHaveLength(1)
    await c.retry()
    expect(f.mock.calls[2][0]).toBe(f.mock.calls[1][0])
    expect(c.getSnapshot()).toMatchObject({ status: 'ready', error: null })
    expect(c.getSnapshot().items).toHaveLength(2)
  })

  it('a failed first page is an error with nothing to show; retry reloads it', async () => {
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValueOnce(page(rows('a'), null))
    const c = make(f)
    await c.start()
    expect(c.getSnapshot()).toMatchObject({ status: 'error', failed: 'first', items: [] })
    await c.retry()
    expect(c.getSnapshot().items).toHaveLength(1)
  })

  it('refresh keeps the old rows on screen until the new first page arrives (stale while revalidate)', async () => {
    let release: (p: PageResponse<Row>) => void = () => {}
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a', 'b'), 'c1'))
      .mockImplementationOnce(() => new Promise((r) => (release = r)))
    const c = make(f)
    await c.start()
    const again = c.refresh()
    expect(c.getSnapshot()).toMatchObject({ status: 'refreshing' })
    expect(c.getSnapshot().items).toHaveLength(2)
    release(page(rows('z'), null))
    await again
    expect(c.getSnapshot().items.map((r) => r.id)).toEqual(['z'])
  })

  it('dispose aborts the request in flight and nothing is written afterwards', async () => {
    let signal!: AbortSignal
    let release: (p: PageResponse<Row>) => void = () => {}
    const f = (_u: string, s: AbortSignal) => {
      signal = s
      return new Promise<PageResponse<Row>>((r) => (release = r))
    }
    const c = make(f)
    const seen = vi.fn()
    c.subscribe(seen)
    const started = c.start()
    seen.mockClear()
    c.dispose()
    expect(signal.aborted).toBe(true)
    release(page(rows('a'), null))
    await started
    expect(c.getSnapshot().items).toEqual([])
    expect(seen).not.toHaveBeenCalled()
  })

  it('can be started again after dispose (React strict mode mounts twice)', async () => {
    const f = vi.fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>().mockResolvedValue(page(rows('a'), null))
    const c = make(f)
    void c.start()
    c.dispose()
    await c.start()
    expect(c.getSnapshot()).toMatchObject({ status: 'ready' })
    expect(c.getSnapshot().items).toHaveLength(1)
  })

  it('a stale cursor restarts the list from the top', async () => {
    const stale = new Error('stale')
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<PageResponse<Row>>>()
      .mockResolvedValueOnce(page(rows('a'), 'c1'))
      .mockRejectedValueOnce(stale)
      .mockResolvedValueOnce(page(rows('a', 'n'), null))
    const c = make(f, { isStaleCursor: (e: unknown) => e === stale })
    await c.start()
    await c.loadMore()
    await vi.waitFor(() => expect(c.getSnapshot().status).toBe('ready'))
    expect(f.mock.calls[2][0]).toBe('/things?limit=2')
    expect(c.getSnapshot().items.map((r) => r.id)).toEqual(['a', 'n'])
  })

  it('an empty list is ready with no rows and no more', async () => {
    const c = make(() => Promise.resolve(page([], null)))
    await c.start()
    expect(c.getSnapshot()).toMatchObject({ status: 'ready', items: [], hasMore: false })
  })

  it('reads rows from a route-specific key such as /notebook\'s `memories`', async () => {
    const f = vi
      .fn<(u: string, s: AbortSignal) => Promise<Record<string, unknown>>>()
      .mockResolvedValueOnce({ memories: rows('a', 'b'), next_cursor: 'c1', has_more: true })
      .mockResolvedValueOnce({ memories: rows('c'), next_cursor: null, has_more: false })
    const c = make(f, { itemsKey: 'memories' })
    await c.start()
    await c.loadMore()
    expect(c.getSnapshot().items.map((r) => r.id)).toEqual(['a', 'b', 'c'])
    expect(c.getSnapshot().hasMore).toBe(false)
  })
})
