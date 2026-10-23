import { renderToStaticMarkup as html } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Badge, Button, Card, DataTable, EmptyState, evenCols, Grid, Meter, Tile, LoadingBlock, Notice, PageHeader, Stat, Stats, type Column } from '@/features/console/ui'

type Row = { a: string }
const columns: Column<Row>[] = [
  { key: 'a', header: 'A', render: (r) => r.a },
  { key: 'x', label: 'Actions', render: () => null },
]

describe('console primitives', () => {
  it('PageHeader renders the one h1 and an optional description', () => {
    const out = html(<PageHeader title="People" description="Who Ava knows" />)
    expect(out).toContain('<h1>People</h1>')
    expect(out).toContain('Who Ava knows')
    expect(html(<PageHeader title="Spend" />)).not.toContain('lede')
  })

  it('Stat puts the label first in the DOM (read first) inside a dl, with equal columns', () => {
    const out = html(
      <Stats label="Counts" cols={3}>
        <Stat label="People" value={3} />
      </Stats>,
    )
    expect(out).toContain('<dl')
    expect(out.indexOf('People')).toBeLessThan(out.indexOf('>3<'))
    expect(out).toContain('--cols:3')
    expect(html(<Stat label="Refused" value={1} tone="danger" />)).toContain('is-danger')
    expect(html(<Stat label="None" value={0} />)).not.toContain('is-')
  })

  it('Badge can carry a dot beside its words, and Notice alerts only for errors', () => {
    expect(html(<Badge tone="ok" dot>Running</Badge>)).toMatch(/c-dot.*Running/)
    expect(html(<Notice tone="danger">Bad</Notice>)).toContain('role="alert"')
    expect(html(<Notice tone="ok">Saved</Notice>)).toContain('role="status"')
  })

  it('Button defaults to type button and maps variants to classes', () => {
    expect(html(<Button>Go</Button>)).toContain('type="button"')
    expect(html(<Button variant="danger">Pause</Button>)).toContain('is-danger')
    expect(html(<Button variant="secondary">More</Button>)).toContain('is-secondary')
    expect(html(<Button type="submit">Send</Button>)).toContain('type="submit"')
  })

  it('Card is a labelled section only when it has a title', () => {
    expect(html(<Card title="Spend" titleId="t">x</Card>)).toMatch(/<section[^>]*aria-labelledby="t"/)
    expect(html(<Card>x</Card>)).toMatch(/^<div/)
    expect(html(<Grid cols={3}>x</Grid>)).toContain('--cols:3')
  })

  it('EmptyState heading level is configurable', () => {
    expect(html(<EmptyState title="No one yet">Add yourself</EmptyState>)).toContain('<h2>No one yet</h2>')
    expect(html(<EmptyState title="Nothing" level={3} />)).toContain('<h3>Nothing</h3>')
  })

  it('DataTable: skeleton while loading, the empty slot with no rows, a named focusable region otherwise', () => {
    const base = { caption: 'Things', columns, rowKey: (r: Row) => r.a }
    expect(html(<DataTable<Row> {...base} rows={null} loadingLabel="Loading things" />)).toContain('Loading things')
    expect(html(<DataTable<Row> {...base} rows={[]} empty={<EmptyState title="None" />} />)).toContain('None')
    const out = html(<DataTable<Row> {...base} rows={[{ a: 'one' }]} maxHeight={200} />)
    expect(out).toContain('tabindex="0"')
    expect(out).toContain('aria-label="Things"')
    expect(out).toContain('aria-label="Actions"')
    expect(out).toContain('max-height:200px')
    expect(out).toContain('one')
  })

  it('LoadingBlock announces what is loading', () => {
    const out = html(<LoadingBlock label="Loading spend" lines={3} />)
    expect(out).toContain('Loading spend')
    expect(out.match(/c-skeleton-line/g)).toHaveLength(3)
  })

  it('evenCols never leaves a tile alone', () => {
    expect([2, 3, 4, 5, 6, 7, 8, 9].map(evenCols)).toEqual([2, 3, 4, 2, 3, 2, 4, 3])
  })

  it('Meter is named and clamps its fill; Tile takes a tone', () => {
    const out = html(<Meter label="Spend" value={20} max={10} />)
    expect(out).toContain('role="meter"')
    expect(out).toContain('aria-label="Spend"')
    expect(out).toContain('width:100%')
    expect(html(<Meter label="Spend" value={0} max={0} />)).toContain('width:0%')
    expect(html(<Tile tone="warn">x</Tile>)).toContain('is-warn')
  })
})
