"use client"

import { useState, type ReactNode, type UIEvent } from "react"
import { computeWindow } from "@/lib/windowing"

/** A long list that keeps only the rows near the viewport in the DOM (ADR 0014). Fixed `rowHeight` (the same
 *  height the skeleton rows use). The container scrolls itself; it is a tab stop so the keyboard can scroll it. */
export function WindowedList<T>({
  items,
  rowHeight,
  height = 480,
  overscan = 6,
  getKey,
  renderRow,
  label,
  className,
}: {
  items: T[]
  rowHeight: number
  height?: number
  overscan?: number
  getKey: (item: T) => string
  renderRow: (item: T, index: number) => ReactNode
  label: string
  className?: string
}) {
  const [scrollTop, setScrollTop] = useState(0)
  const w = computeWindow({ count: items.length, rowHeight, scrollTop, viewport: height, overscan })
  return (
    <div
      role="list"
      aria-label={label}
      tabIndex={0}
      className={className ?? "overflow-y-auto rounded-md border focus-visible:outline-2 focus-visible:outline-offset-2"}
      style={{ height: Math.min(height, Math.max(w.total, rowHeight)) }}
      onScroll={(e: UIEvent<HTMLDivElement>) => setScrollTop(e.currentTarget.scrollTop)}
    >
      <div style={{ paddingTop: w.padTop, paddingBottom: w.padBottom }}>
        {items.slice(w.start, w.end).map((item, i) => (
          <div
            key={getKey(item)}
            role="listitem"
            aria-posinset={w.start + i + 1}
            aria-setsize={items.length}
            style={{ height: rowHeight }}
          >
            {renderRow(item, w.start + i)}
          </div>
        ))}
      </div>
    </div>
  )
}
