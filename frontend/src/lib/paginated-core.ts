// The state machine behind usePaginated (ADR 0014). No React in here, so it runs under vitest in plain node and the
// hook is a thin subscription to it. One controller is one list: one path, one set of filters.
//
//   loading        first page, nothing to show yet (render skeletons)
//   ready          items on screen, maybe more
//   refreshing     a reload of the same list; the old items stay on screen until the new first page lands
//   loading-more   the next page is on its way; the items already shown stay
//   error          the last request failed; `failed` says whether it was the first page or a later one
//
// A change of filters is a new controller (the old one is disposed and its requests aborted), never a reset of
// this one, so a slow answer for the old filter can't land on the new list.

export type PageResponse<T> = {
  items: T[]
  next_cursor: string | null
  has_more: boolean
  total_estimate?: number | null
}

export type Status = 'loading' | 'ready' | 'refreshing' | 'loading-more' | 'error'

export type PaginatedState<T> = {
  items: T[]
  status: Status
  error: string | null
  failed: 'first' | 'more' | null
  hasMore: boolean
  totalEstimate: number | null
}

export type Fetcher<T> = (path: string, signal: AbortSignal) => Promise<PageResponse<T>>

export type ControllerOptions<T> = {
  path: string
  limit: number
  params?: Record<string, string | number | boolean | null | undefined>
  fetcher: Fetcher<T>
  getId: (item: T) => string
  describeError: (e: unknown) => string
  // The server refuses a cursor it can't trust or that expired (422 invalid_cursor): the list starts over.
  isStaleCursor?: (e: unknown) => boolean
}

export function pageUrl(
  path: string,
  limit: number,
  cursor: string | null,
  params: ControllerOptions<unknown>['params'] = {},
): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') q.set(k, String(v))
  q.set('limit', String(limit))
  if (cursor) q.set('cursor', cursor)
  return `${path}${path.includes('?') ? '&' : '?'}${q.toString()}`
}

const empty = <T>(): PaginatedState<T> => ({
  items: [],
  status: 'loading',
  error: null,
  failed: null,
  hasMore: false,
  totalEstimate: null,
})

export class PaginatedController<T> {
  private state: PaginatedState<T> = empty<T>()
  private cursor: string | null = null
  private abort: AbortController | null = null
  private listeners = new Set<() => void>()
  private disposed = false
  private generation = 0

  constructor(private readonly o: ControllerOptions<T>) {}

  // useSyncExternalStore contract: a stable snapshot until something changes.
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
  getSnapshot = (): PaginatedState<T> => this.state

  private set(next: Partial<PaginatedState<T>>) {
    this.state = { ...this.state, ...next }
    this.listeners.forEach((fn) => fn())
  }

  /** Fetch the first page. Safe to call again: a second call while one is running is ignored. Also revives a
   *  disposed controller (React strict mode mounts, unmounts and mounts an effect once in development). */
  start(): Promise<void> {
    this.disposed = false
    return this.first(this.state.items.length ? 'refreshing' : 'loading')
  }

  /** Reload from the first page, keeping the current rows on screen until the new ones arrive. */
  refresh(): Promise<void> {
    return this.first(this.state.items.length ? 'refreshing' : 'loading', true)
  }

  /** Try again after an error: the first page if that is what failed, else the page after the last one shown. */
  retry(): Promise<void> {
    return this.state.failed === 'more' ? this.loadMore() : this.first(this.state.items.length ? 'refreshing' : 'loading', true)
  }

  /** The next page. Ignored while a request is running or when the list has ended (so a double click or an
   *  observer firing twice asks the server once). */
  loadMore(): Promise<void> {
    const s = this.state
    if (this.disposed || !this.cursor || !s.hasMore) return Promise.resolve()
    if (s.status === 'loading' || s.status === 'refreshing' || s.status === 'loading-more') return Promise.resolve()
    this.set({ status: 'loading-more', error: null, failed: null })
    return this.run(this.cursor, false)
  }

  /** Stop: aborts the request in flight. Until `start()` is called again the controller never writes state. */
  dispose(): void {
    this.disposed = true
    this.abort?.abort()
    this.abort = null
    this.generation++
  }

  private first(status: Status, force = false): Promise<void> {
    if (this.disposed) return Promise.resolve()
    const busy = this.state.status === 'loading' || this.state.status === 'refreshing'
    if (busy && this.abort && !force) return Promise.resolve()
    this.set({ status, error: null, failed: null })
    return this.run(null, true)
  }

  private async run(cursor: string | null, replace: boolean): Promise<void> {
    this.abort?.abort()
    const ctl = new AbortController()
    this.abort = ctl
    const mine = ++this.generation
    try {
      const page = await this.o.fetcher(pageUrl(this.o.path, this.o.limit, cursor, this.o.params), ctl.signal)
      if (this.disposed || mine !== this.generation) return
      const seen = new Set(replace ? [] : this.state.items.map(this.o.getId))
      const fresh = page.items.filter((it) => {
        const id = this.o.getId(it)
        if (seen.has(id)) return false // an overlapping or retried page never duplicates a row
        seen.add(id)
        return true
      })
      this.cursor = page.next_cursor
      this.set({
        items: replace ? fresh : [...this.state.items, ...fresh],
        status: 'ready',
        error: null,
        failed: null,
        hasMore: page.has_more && page.next_cursor !== null,
        totalEstimate: page.total_estimate ?? null,
      })
    } catch (e) {
      if (this.disposed || mine !== this.generation || ctl.signal.aborted) return
      if (!replace && this.o.isStaleCursor?.(e)) {
        // The cursor aged out: start again from the top instead of showing a dead error.
        this.cursor = null
        void this.first(this.state.items.length ? 'refreshing' : 'loading', true)
        return
      }
      this.set({ status: 'error', error: this.o.describeError(e), failed: replace ? 'first' : 'more' })
    }
  }
}
