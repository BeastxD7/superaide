"use client"

import { Button } from "@/components/ui/button"

/** What sits under a paginated list: Load more, "End of list", or the error with Retry (ADR 0014).
 *  `usePaginated(...).footer` has exactly these props. A polite live region announces the change. */
export function ListFooter({
  hasMore,
  loading,
  error,
  empty = false,
  onLoadMore,
  onRetry,
  endLabel = "End of list",
  className,
}: {
  hasMore: boolean
  loading: boolean
  error: string | null
  empty?: boolean
  onLoadMore: () => void
  onRetry: () => void
  endLabel?: string
  className?: string
}) {
  if (empty) return null // the page shows its own empty state
  return (
    <div className={className ?? "flex min-h-12 items-center justify-center py-3 text-sm text-muted-foreground"} data-slot="list-footer">
      <div aria-live="polite" className="flex flex-wrap items-center justify-center gap-3">
        {error ? (
          <>
            <span role="alert" className="text-danger">{`Couldn't load more. ${error}`}</span>
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          </>
        ) : hasMore ? (
          <Button type="button" size="sm" variant="outline" disabled={loading} aria-busy={loading} onClick={onLoadMore}>
            {loading ? "Loading..." : "Load more"}
          </Button>
        ) : (
          <span>{endLabel}</span>
        )}
      </div>
    </div>
  )
}
