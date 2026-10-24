# Operator console design spec

Status: accepted (2026-10-23). Applies to `frontend/src/features/console/**` and `frontend/src/styles/console.css`.

The console is a tool for one operator, so it favours scanning over decoration, but it shares the landing page's
visual language: white canvas, deep violet ink, one violet accent, lilac panels, Geist, pill controls, 28px panels,
glass bar and the `cubic-bezier(0.22, 1, 0.36, 1)` easing. It keeps its own root layout and stylesheet (no Tailwind,
no site CSS), so a console change never touches the site and the reverse (`e2e/tests/console/console.spec.ts`, "separate
from the site").

## Audit of the old console (what we fixed)

| Finding | Where | Fix |
|---|---|---|
| Content sat left-aligned in a 1120px column with an empty right side | every page | one centred 1152px container, the bar and the content share its edges |
| Three header styles (title bar, underlined tabs, hero h1) and no h1 on five of six pages | shell | the app's sidebar plus a slim bar, one `PageHeader` with the page's only h1 |
| Four table treatments, a different row height each, unlabelled scroll regions | People, Spend | `DataTable`: 40px header, 52px rows, sticky header, focusable scroll region |
| Stats were label/number rows, uneven columns (5 against 6 rows) | Overview | `Stat` tiles in equal columns (page PRs 2 to 4) |
| Slate blue accent unrelated to the product, a different font (Atkinson) | everywhere | violet tokens, Geist |
| Pause state shown by a 3px red line | top bar | status badge plus a banner with the words |
| Buttons: outlined, filled and underlined links with no scale | everywhere | `Button` variants |
| Loading was a grey sentence, empty states had no frame | every page | `LoadingBlock` skeleton, `EmptyState` panel |
| Phone: the bar and tabs wrapped unpredictably | shell | the app's sidebar as a drawer, pause switch kept in the top bar |

## Grid and spacing

- Container: max 1152px content width, 24px gutter (16px under 720px). The bar and `main` use the same
  `.c-container`, so their edges align.
- Columns: `Grid` and `Stats` take 2, 3 or 4 equal columns (`repeat(n, minmax(0, 1fr))`, one 24px or 16px gap).
  Choose a count that fills every row; never leave a tile alone. Under 720px `Grid` is one column and `Stats` two.
- Spacing scale (tokens `--s-1` to `--s-7`): 4, 8, 12, 16, 24, 32, 48. Nothing else.
  - 32 between the bar and the page, and under the page header; 24 between cards; 16 inside cards between blocks
    and between stat tiles; 24 card padding (16 on phones); 8 between controls.
- Vertical rhythm: every page is `PageHeader` then blocks separated by 24.

## Type scale (Geist, tabular numbers for data)

| Use | Size / weight |
|---|---|
| Page title (h1) | clamp(28px, 4vw, 40px), 600, -0.035em |
| Card title (h2) | 18px, 600 |
| Sub-heading (h3) | 15px, 600 |
| Body | 15px, 400, line height 1.55 |
| Table body, notices | 14px |
| Labels, badges, table header | 12 to 13px, 500 |
| Stat number | 32px, 600, tabular |

## Colour (tokens in `console.css`, light only like the app)

| Token | Value | Use |
|---|---|---|
| `--canvas` | #ffffff | page |
| `--mist` / `--haze` | #f7f6fc / #f2f1f8 | tiles, empty states |
| `--ink` / `--body` / `--muted` | #1b1638 / #4a4566 / #6a658d | headings, text, secondary text |
| `--line` | #e7e4f3 | hairlines |
| `--accent` | #6747ee | buttons, meters (white text 5.2:1) |
| `--accent-text` | #5232d4 | violet used as text |

Status colours always sit on their own soft background, and carry words or a dot, never colour alone:

| Status | Text on soft background | Contrast |
|---|---|---|
| ok | #17714a on #e6f5ee | 5.1:1 |
| warn | #8a5000 on #fdf1dc | 6.1:1 |
| danger | #b3304e on #fdecef | 5.7:1 |
| info and accent | #5232d4 on #f3edff | 6.6:1 |

axe runs on every tab, collapsed and expanded and in the phone drawer
(`e2e/tests/web-console/console-ui.spec.ts`); it is the check of record.

## Shell

The console uses the same shadcn Sidebar as the app (`components/ui/sidebar.tsx`), so the two feel like one product
(owner decision, 2026-10-23). `ConsoleSidebar.tsx` and `ConsoleApp.tsx` build it:

- `SidebarProvider` + `Sidebar` (`collapsible="icon"`, `variant="inset"`) + `SidebarInset`. The open state uses the app's
  `sidebar_state` cookie, so both remember it. Press Ctrl or Cmd + b to collapse to icons.
- Header: the orb, "Superaide" and "Operator console", then the agent card (orb, name, running or paused, stage).
- Menu inside `nav[aria-label="Sections"]`, two groups: Monitor (Overview, Spend) and Owner's content, needs a reason
  (People, Conversations, Safety log, Try an impostor). Items are buttons named as before, `aria-current="page"` on
  the open one, a lucide icon each and a tooltip when collapsed.
- Footer: a line saying the admin token stays in this browser, and Disconnect.
- Slim top bar in the inset: `SidebarTrigger`, breadcrumb (Console / page), the Running or Paused badge and the
  pause or resume switch. The switch stays in the bar on phones: the emergency stop is never behind a drawer.
- When paused, a banner under the bar repeats the plain words "Paused: no model calls, no replies".
- Phones (under 768px): the sidebar is the Sheet drawer, closed at first. Escape closes it, choosing a page closes it,
  focus returns to the trigger. No tooltips in the drawer (a focused item's hidden tooltip swallowed the first Escape).
- Content: one centred `.c-container` (max 1152px, 24px gutter, 16px on phones) inside the inset.

The console keeps its own root layout (a full page load from the site, no session providers) but loads `app.css`, so it
shares the site's tokens, Geist and the shadcn components. `console.css` is wrapped in `@layer components`, so Tailwind
utilities on shared components always beat it. The app is light only, so the console has no dark theme.

## Patterns (components in `features/console/ui/`)

- `PageHeader`: the page's h1, a one-line description, optional actions. Exactly one per page.
- `Card`: 28px panel with an optional h2, description and actions; `tinted` for a quiet panel. `Grid` lays cards out.
- `Stat` and `Stats`: number above a small label, minimum height 128px, and every row of one `Stats` is the same height, equal widths; `tone` warn or danger tints the tile.
- `DataTable`: columns and rows as data; header 40px, rows 52px, sticky header, hairlines, no zebra; `maxHeight` scrolls
  inside; the scroll region is focusable and named. Loading shows a skeleton, no rows shows the `empty` slot.
- `Badge`: neutral, ok, warn, danger, accent; `dot` for a leading mark.
- `Button`: primary (violet), secondary, ghost, danger and ok (outlined). Destructive actions that change data confirm
  first; the pause switch does not, because it is the emergency stop and must be one press.
- `Toolbar` and `Chip`: filters as toggle buttons with `aria-pressed`.
- `Notice`: info, ok, warn, danger. Danger is `role="alert"`; the rest `role="status"`.
- `EmptyState`: dashed panel, h2, one sentence saying what it means and what to do.
- `LoadingBlock`: shimmering lines with a screen-reader label.
- Toasts: the console has no transient toasts; results and errors are inline `Notice`s next to the control.

## Loading states

`LoadingBlock` is built on the shared `Skeleton`. When the pagination work's skeleton set (`SkeletonLine`, `SkeletonTableRows`) and `usePaginated` are on main, the console tables adopt them page by page as the paged operator routes appear.

## Motion

Subtle only: a 500ms rise on page change, 200ms colour transitions, a shimmer on skeletons. All of it is off under
`prefers-reduced-motion: reduce`.

## Accessibility

Focus ring: 2px violet on everything (an outline, or the sidebar's ring). One h1 per page, h2 for cards, h3 below. Tables have captions
and scoped headers. Icon-only columns carry an `aria-label`. Targets are at least 32px high. Keyboard order follows
the visual order: sidebar items, Disconnect, the sidebar trigger, Pause, then the page.

## Rollout

1. Spec, primitives, shell, connect screen (this change); pages keep their markup with legacy rules restyled.
2. Overview and Spend.
3. People, Conversations and the Safety log.
4. Simulator, break-glass and remaining polish; the legacy CSS block is deleted.
