import { cn } from "@/lib/utils"

// The placeholder for content that is on its way. The shimmer is a pulse that stops under
// prefers-reduced-motion (the shape stays, so the layout still holds). Every primitive below takes the size of
// the real content it stands in for, so nothing moves when the data arrives (ADR 0014).
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-accent motion-reduce:animate-none", className)}
      {...props}
    />
  )
}

/** One line of text. `height` is the real line height in px (a 14px text with leading-5 is 20). */
function SkeletonLine({ width = "100%", height = 16, className }: { width?: string | number; height?: number; className?: string }) {
  return <Skeleton data-skeleton="line" className={className} style={{ width, height }} />
}

/** A block of known size: an image, a chart, a card body. */
function SkeletonBlock({ width = "100%", height, className }: { width?: string | number; height: number; className?: string }) {
  return <Skeleton data-skeleton="block" className={className} style={{ width, height }} />
}

/** A list row: optional round avatar, a title line and a detail line, in a box of the real row's `height`. */
function SkeletonRow({ height = 64, avatar = true, className }: { height?: number; avatar?: boolean; className?: string }) {
  return (
    <div data-skeleton="row" className={cn("flex items-center gap-3", className)} style={{ height }}>
      {avatar ? <Skeleton className="size-10 shrink-0 rounded-full" /> : null}
      <div className="min-w-0 flex-1 space-y-2">
        <SkeletonLine width="55%" height={14} />
        <SkeletonLine width="85%" height={12} />
      </div>
    </div>
  )
}

/** A card with a title and `lines` lines of body; `height` is the real card's height. */
function SkeletonCard({ height = 112, lines = 2, className }: { height?: number; lines?: number; className?: string }) {
  return (
    <div data-skeleton="card" className={cn("flex flex-col justify-center gap-3 rounded-xl border bg-card px-5", className)} style={{ height }}>
      <SkeletonLine width="40%" height={16} />
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonLine key={i} width={i === lines - 1 ? "70%" : "100%"} height={12} />
      ))}
    </div>
  )
}

/** `rows` table rows of `columns` cells, each `rowHeight` tall like the real ones. Render inside a <tbody>. */
function SkeletonTableRows({ rows = 5, columns = 4, rowHeight = 44 }: { rows?: number; columns?: number; rowHeight?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} data-skeleton="table-row" style={{ height: rowHeight }}>
          {Array.from({ length: columns }, (_, c) => (
            <td key={c} className="px-3">
              <SkeletonLine width={c === 0 ? "70%" : "50%"} height={12} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

/** A whole loading list: `count` rows or cards, announced once to screen readers as "Loading <what>". */
function SkeletonList({
  count = 5,
  variant = "row",
  rowHeight,
  what = "content",
  className,
}: {
  count?: number
  variant?: "row" | "card"
  rowHeight?: number
  what?: string
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn(variant === "card" ? "space-y-3" : "divide-y", className)}>
      <span className="sr-only">{`Loading ${what}`}</span>
      {Array.from({ length: count }, (_, i) =>
        variant === "card" ? <SkeletonCard key={i} height={rowHeight} /> : <SkeletonRow key={i} height={rowHeight} />,
      )}
    </div>
  )
}

export { Skeleton, SkeletonLine, SkeletonBlock, SkeletonRow, SkeletonCard, SkeletonTableRows, SkeletonList }
