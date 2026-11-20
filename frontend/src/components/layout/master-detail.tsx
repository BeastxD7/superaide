import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

// A list on the left and the chosen item on the right (Approvals, Notebook, People). It fills the room under the app
// header and each pane scrolls on its own, so the list keeps its place while you read a long item and the item's
// buttons never scroll away with the list.
//
// Wide (the container is at least `@3xl`, 48rem): both panes side by side. Narrow: the list alone, and once an item is
// chosen (`selected`) the detail alone, with the page's own Back control calling `onBack`. Which item is chosen
// belongs in the URL or in page state; this component only lays it out.
//
// `context` is an optional third pane for related facts. It appears from `@7xl` (80rem) up.
export function MasterDetail({
  list,
  listLabel,
  detail,
  detailLabel,
  selected,
  empty,
  context,
  contextLabel = 'Related',
  header,
  listWidth = 'md',
  className,
}: {
  list: ReactNode
  listLabel: string
  detail: ReactNode
  detailLabel: string
  selected: boolean
  empty?: ReactNode // shown in the detail pane when nothing is chosen (wide screens only)
  context?: ReactNode
  contextLabel?: string
  header?: ReactNode // fixed above both panes
  listWidth?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const width = { sm: '@3xl/md:w-72', md: '@3xl/md:w-80 @5xl/md:w-96', lg: '@3xl/md:w-96 @5xl/md:w-[28rem]' }[listWidth]
  return (
    <div data-master-detail data-selected={selected} className={cn('@container/md flex min-h-0 flex-1 flex-col', className)}>
      {header && <div className="shrink-0 px-4 pt-6 sm:px-6 lg:px-8 lg:pt-8">{header}</div>}
      <div className="flex min-h-0 flex-1">
        <section
          aria-label={listLabel}
          data-pane="list"
          tabIndex={0}
          className={cn(
            'min-h-0 w-full shrink-0 overflow-y-auto border-line/70 px-4 pb-6 sm:px-6 @3xl/md:border-r @3xl/md:px-4 lg:pb-8',
            width,
            selected ? 'hidden @3xl/md:block' : 'block',
          )}
        >
          {list}
        </section>
        <section
          aria-label={detailLabel}
          data-pane="detail"
          tabIndex={0}
          className={cn('min-h-0 min-w-0 flex-1 overflow-y-auto focus-visible:outline-2 focus-visible:-outline-offset-2 px-4 pb-6 sm:px-6 @3xl/md:px-8 lg:pb-8', selected ? 'block' : 'hidden @3xl/md:block')}
        >
          {selected ? detail : empty}
        </section>
        {context && (
          <aside
            aria-label={contextLabel}
            data-pane="context"
            className="hidden min-h-0 w-80 shrink-0 overflow-y-auto border-l border-line/70 px-4 pb-6 @7xl/md:block lg:pb-8"
          >
            {context}
          </aside>
        )}
      </div>
    </div>
  )
}
