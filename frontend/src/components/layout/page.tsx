import type { ComponentProps, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/utils'

// Layout primitives for the signed-in app (issue #663). A page picks a width for what it shows instead of
// copying `mx-auto max-w-3xl` around:
//
//   reading  48rem   prose and single-column forms: lines stay at a comfortable measure
//   form     56rem   settings-style pages with labelled fields
//   list     96rem   cards and lists that may sit two or three across
//   wide     120rem  dashboards, tables and boards; stops here (1920px) and is centred beyond it,
//                    so the owner's 1920 screens are filled and a 2560 one keeps calm margins
//   full     none    master-detail screens and canvases that own the whole inset
//
// The content box switches layouts on its own width (container queries, `@3xl/page:`...) and not on the window's:
// the sidebar can be open or collapsed, so the window width says little about the room a page has.
export type PageWidth = 'reading' | 'form' | 'list' | 'wide' | 'full'

const PAGE_MAX: Record<PageWidth, string> = {
  reading: 'max-w-3xl',
  form: 'max-w-4xl',
  list: 'max-w-[96rem]',
  wide: 'max-w-[120rem]',
  full: 'max-w-none',
}

// The scrolling page: it owns the scroller (`min-h-0 flex-1 overflow-y-auto`), the gutters and the width. `aside` is an
// optional right-hand context panel; it sits beside the content from the `@5xl` container width up (and stays in view
// while the page scrolls), and drops below the content on narrower screens.
export function PageContainer({
  width = 'list',
  aside,
  asideLabel = 'Related',
  className,
  children,
}: {
  width?: PageWidth
  aside?: ReactNode
  asideLabel?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div data-page-container data-width={width} className="@container/page min-h-0 flex-1 overflow-y-auto">
      <div className={cn('mx-auto w-full px-4 py-6 sm:px-6 lg:px-8 lg:py-8', PAGE_MAX[width], className)}>
        {aside ? (
          <div className="grid gap-8 @5xl/page:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="min-w-0">{children}</div>
            <aside aria-label={asideLabel} data-page-aside className="min-w-0 @5xl/page:sticky @5xl/page:top-0 @5xl/page:self-start">
              {aside}
            </aside>
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  )
}

// A page title with an optional line under it and actions beside it. `sticky` keeps it (and the actions) at the top of
// the scrolling page, for long lists; it then uses a smaller title and no description.
export function PageHeader({
  title,
  description,
  actions,
  sticky = false,
  as: Heading = 'h1',
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  sticky?: boolean
  as?: 'h1' | 'h2' // a page that shows another h1 beside this one (a list with a person open) uses h2
  className?: string
}) {
  return (
    <header
      data-page-header
      data-sticky={sticky || undefined}
      className={cn(
        'flex flex-wrap items-start justify-between gap-x-4 gap-y-3',
        sticky &&
          'sticky top-0 z-10 -mx-4 -mt-6 mb-4 items-center border-b border-line/70 bg-canvas/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:-mt-8 lg:px-8',
        className,
      )}
    >
      <div className="min-w-0">
        <Heading className={cn('font-medium text-ink tight', sticky ? 'text-2xl' : 'text-3xl')}>{title}</Heading>
        {description && !sticky && <p className="mt-1 max-w-[70ch] text-body">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

// Cards in as many columns as fit: each column is at least `min` wide, so a phone gets one, a laptop two and a large
// monitor three or four, with no breakpoints to maintain. `fit` stretches the last cards over any empty tracks
// instead of leaving them blank (use it for one or two big cards).
export function ResponsiveGrid<T extends ElementType = 'div'>({
  as,
  min = '18rem',
  fit = false,
  className,
  style,
  ...props
}: { as?: T; min?: string; fit?: boolean } & Omit<ComponentProps<T>, 'as' | 'min'>) {
  const Tag = (as ?? 'div') as ElementType
  return (
    <Tag
      data-responsive-grid
      className={cn('grid gap-3 @3xl/page:gap-4', className)}
      style={{ gridTemplateColumns: `repeat(${fit ? 'auto-fit' : 'auto-fill'}, minmax(min(100%, ${min}), 1fr))`, ...style }}
      {...props}
    />
  )
}
