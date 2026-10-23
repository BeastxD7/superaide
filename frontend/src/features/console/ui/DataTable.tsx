import type { CSSProperties, Key, ReactNode } from 'react'
import { LoadingBlock } from '@/features/console/ui/Skeleton'

export type Column<T> = {
  key: string
  /** Visible heading. Leave out for an icon-only column and give `label` instead. */
  header?: ReactNode
  /** The accessible name when there is no visible heading, e.g. "Actions". */
  label?: string
  align?: 'left' | 'right'
  render: (row: T) => ReactNode
}

type Props<T> = {
  /** Names the table for screen readers. */
  caption: string
  columns: Column<T>[]
  rows: T[] | null
  rowKey: (row: T) => Key
  rowClass?: (row: T) => string | undefined
  /** Shown instead of the table when there are no rows. */
  empty?: ReactNode
  loadingLabel?: string
  /** Cap the height and scroll inside, so the sticky header stays in view on long tables. */
  maxHeight?: number
}

// One table for the whole console: 40px header, 52px rows, hairlines, sticky header, scrolls sideways on phones.
export function DataTable<T>({ caption, columns, rows, rowKey, rowClass, empty, loadingLabel = 'Loading', maxHeight }: Props<T>) {
  if (rows === null) return <LoadingBlock label={loadingLabel} />
  if (rows.length === 0 && empty) return <>{empty}</>
  const style: CSSProperties | undefined = maxHeight ? { maxHeight } : undefined
  return (
    // A scrollable region must be reachable by keyboard (axe: scrollable-region-focusable).
    <div className="c-table-wrap" style={style} tabIndex={0} role="region" aria-label={caption}>
      <table className="c-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.align === 'right' ? 'r' : undefined} aria-label={c.header ? undefined : c.label}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className={rowClass?.(row)}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === 'right' ? 'r' : undefined}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
