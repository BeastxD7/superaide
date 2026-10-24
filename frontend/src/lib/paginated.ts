'use client'

// usePaginated: one cursor-paginated list (ADR 0014). `path` is the endpoint under /app (for example '/notebook'),
// `params` its filters. Change either and the list starts over from page one (the old requests are aborted);
// unmounting aborts too. The rows already shown stay on screen while the next page or a refresh loads.
//
//   const list = usePaginated<Memory>('/notebook', { limit: 50 })
//   list.status === 'loading'  -> <SkeletonList ... />
//   list.items                 -> rows
//   <ListFooter {...list.footer} />   (Load more / End of list / Retry)
import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { errorMessage } from '@/lib/action'
import { AppError, call, callPage } from '@/lib/api'
import {
  PaginatedController,
  type Fetcher,
  type PageResponse,
  type PaginatedState,
  type ControllerOptions,
} from '@/lib/paginated-core'

export type { PageResponse, PaginatedState, Fetcher }

export type UsePaginatedOptions<T> = {
  limit?: number
  params?: ControllerOptions<T>['params']
  /** How to read an item's identity. Default: `item.id`. */
  getId?: (item: T) => string
  /** Another transport (the operator console's bearer-token client). Default: the signed-in /app client. */
  fetcher?: Fetcher<T>
  /** The response key holding the rows, for a route that predates the standard (`memories` for /notebook). */
  itemsKey?: string
  /** false: do not request anything yet (a filter is not ready). */
  enabled?: boolean
}

const appFetcher = <T,>(path: string, signal: AbortSignal): Promise<PageResponse<T>> => call<PageResponse<T>>(path, { signal })

/** For an /app route that answers a bare array and pages by header (`X-Next-Cursor`, `X-Has-More`), such as /access-log. */
export const arrayAppFetcher = async <T,>(path: string, signal: AbortSignal): Promise<PageResponse<T>> => {
  const { data, hasMore, nextCursor } = await callPage<T[]>(path, { signal })
  return { items: data, next_cursor: nextCursor, has_more: hasMore ?? false }
}

const staleCursor = (e: unknown) => e instanceof AppError && e.code === 'invalid_cursor'

const defaultId = (item: unknown): string => String((item as { id: string | number }).id)

export function usePaginated<T>(path: string, opts: UsePaginatedOptions<T> = {}) {
  const { limit = 25, params, getId = defaultId, fetcher, itemsKey, enabled = true } = opts
  const paramKey = JSON.stringify(params ?? {})
  const stableParams = useMemo(() => JSON.parse(paramKey) as NonNullable<typeof params>, [paramKey])
  const controller = useMemo(
    () =>
      new PaginatedController<T>({
        path,
        limit,
        params: stableParams,
        fetcher: fetcher ?? (appFetcher as Fetcher<T>),
        getId,
        itemsKey,
        describeError: errorMessage,
        isStaleCursor: staleCursor,
      }),
    // `getId` is read once; pass a module-level function (or none). Filters compare by value through paramKey.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
    [path, limit, stableParams, fetcher, itemsKey],
  )

  useEffect(() => {
    if (!enabled) return
    void controller.start()
    return () => controller.dispose()
  }, [controller, enabled])

  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const footer = {
    hasMore: state.hasMore,
    loading: state.status === 'loading-more',
    error: state.failed === 'more' ? state.error : null,
    onLoadMore: () => void controller.loadMore(),
    onRetry: () => void controller.retry(),
    empty: state.status === 'ready' && state.items.length === 0,
  }
  return {
    ...state,
    footer,
    loadMore: () => controller.loadMore(),
    retry: () => controller.retry(),
    /** Start over from page one; the rows on screen stay until the fresh first page lands. */
    refresh: () => controller.refresh(),
  }
}
