'use client'

import { ArrowLeft, ShieldQuestion } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { MasterDetail, PageContainer, PageHeader } from '@/components/layout'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ApprovalActions, ApprovalBody } from '@/features/dashboard/approvals/Approvals'
import { KIND_LABEL, useApprovalDecisions } from '@/features/dashboard/approvals/decisions'
import { BatchCard } from '@/features/dashboard/approvals/BatchCard'
import { BurdenCard } from '@/features/dashboard/approvals/BurdenCard'
import { MandatesCard } from '@/features/dashboard/approvals/MandatesCard'
import { seenBeacon } from '@/features/dashboard/approvals/seen'
import { TwinCard } from '@/features/dashboard/approvals/TwinCard'
import { useApprovals } from '@/features/dashboard/approvals/useApprovals'
import { useSession } from '@/lib/session-context'
import { useMedia } from '@/hooks/use-media'
import type { ApprovalItem } from '@/lib/api'
import { announceApprovalsChanged } from '@/lib/approvals-sync'
import { formatWhen } from '@/lib/dates'

// The chosen request lives in the address (?a=<id>) so it can be linked and Back works; nothing else is stored there.
function useChosen() {
  const read = () => (typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('a'))
  const [id, setId] = useState<string | null>(read)
  useEffect(() => {
    const onPop = () => setId(read())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const choose = useCallback((next: string | null) => {
    const url = new URL(window.location.href)
    if (next) url.searchParams.set('a', next)
    else url.searchParams.delete('a')
    window.history.pushState(null, '', url)
    setId(next)
  }, [])
  return [id, choose] as const
}

const STATUS_LABEL: Record<ApprovalItem['status'], string> = { pending: 'Waiting', approved: 'Approved', rejected: 'Said no', expired: 'Expired' }

// The title of a row: what it is, and for an email who it goes to.
function rowTitle(a: ApprovalItem) {
  const to = a.fields?.find((f) => f.label === 'To')?.value
  return to ? `${KIND_LABEL[a.kind] ?? a.kind}: ${to}` : (KIND_LABEL[a.kind] ?? a.kind)
}

// Approvals (ADR 0011 section 2, #435): the requests as a list, the chosen one beside it with the buttons in reach.
export function ApprovalsPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, reload, waiting: allWaiting, expired, batches } = useApprovals()
  const [view, setView] = useState<'requests' | 'rules'>('requests')
  const [tab, setTab] = useState<'waiting' | 'decided'>('waiting')
  const [chosen, choose] = useChosen()
  const wide = useMedia('(min-width: 1024px)')
  const d = useApprovalDecisions(reload)

  const grouped = useMemo(() => new Set(batches.flatMap((b) => b.items.map((i) => i.id))), [batches])
  const waiting = useMemo(() => allWaiting.filter((a) => !grouped.has(a.id)), [allWaiting, grouped])
  const decided = useMemo(() => (data ?? []).filter((a) => a.status === 'approved' || a.status === 'rejected'), [data])
  const rows = tab === 'waiting' ? waiting : decided
  // A chosen request that is gone (decided, or no longer in this tab) falls back; on a wide screen the first one shows.
  const current = rows.find((a) => a.id === chosen) ?? (wide ? rows[0] : undefined)

  function pick(next: 'waiting' | 'decided') {
    setTab(next)
    if (chosen) choose(null)
  }

  const header = (
    <PageHeader
      title="Approvals"
      description={`Things ${name} was asked to do that need your yes first.`}
      actions={
        <div role="tablist" aria-label="Approvals sections" className="flex rounded-lg border border-line bg-mist p-0.5 text-sm">
          {(
            [
              ['requests', 'Requests'],
              ['rules', 'Rules and insights'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              onClick={() => setView(key)}
              className={`min-h-11 rounded-md px-3 ${view === key ? 'bg-white font-medium text-ink shadow-sm' : 'text-body hover:text-ink'}`}
            >
              {label}
            </button>
          ))}
        </div>
      }
    />
  )

  if (view === 'rules') {
    return (
      <PageContainer width="form">
        {header}
        <BurdenCard />
        <MandatesCard name={name} />
        <TwinCard name={name} />
      </PageContainer>
    )
  }

  const list = (
    <div data-tour="approvals-list" className="pt-4">
      <div role="tablist" aria-label="Requests" className="mb-3 flex gap-1 border-b border-line">
        {(
          [
            ['waiting', 'Waiting', allWaiting.length],
            ['decided', 'Decided', decided.length],
          ] as const
        ).map(([key, label, n]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => pick(key)}
            className={`-mb-px flex min-h-11 items-center gap-1.5 border-b-2 px-3 text-sm ${
              tab === key ? 'border-violet-600 font-medium text-violet-700' : 'border-transparent text-body hover:text-ink'
            }`}
          >
            {label} <Badge variant="secondary">{n}</Badge>
          </button>
        ))}
      </div>
      {d.error && (
        <div className="mb-3">
          <Notice tone="error">{d.error}</Notice>
        </div>
      )}
      {tab === 'waiting' && (allWaiting.length > 0 || expired.length === 0) && (
        <h2 className="mb-1 flex items-center gap-2 text-sm font-medium text-ink">
          <ShieldQuestion className="size-4 text-violet-600" aria-hidden />
          {allWaiting.length ? 'Waiting for your OK' : data ? 'Nothing is waiting for your OK.' : 'Loading requests'}
        </h2>
      )}
      {tab === 'waiting' && allWaiting.length > 0 && <p className="mb-3 text-xs text-muted">{name} won&apos;t do any of this until you say yes.</p>}
      {tab === 'waiting' &&
        batches.map((b) => (
          <ul key={b.kind} className="mb-3">
            <BatchCard
              batch={b}
              onError={d.setError}
              onDone={async () => {
                await reload()
                announceApprovalsChanged()
              }}
            />
          </ul>
        ))}
      <ul aria-label={tab === 'waiting' ? 'Waiting requests' : 'Decided requests'} className="space-y-1.5">
        {rows.map((a) => {
          const on = current?.id === a.id
          return (
            <li key={a.id}>
              <button
                type="button"
                aria-current={on ? 'true' : undefined}
                onClick={() => choose(a.id)}
                className={`flex w-full min-w-0 flex-col gap-0.5 rounded-xl border p-3 text-left transition-colors ${
                  on ? 'border-violet-400 bg-lilac-50' : 'border-line bg-white hover:bg-mist'
                }`}
              >
                <span className="flex items-start justify-between gap-2">
                  <span className="min-w-0 text-sm font-medium text-ink [overflow-wrap:anywhere]">{rowTitle(a)}</span>
                  <Badge variant={a.status === 'rejected' ? 'outline' : 'secondary'} className="shrink-0">
                    {STATUS_LABEL[a.status]}
                  </Badge>
                </span>
                {!on && <span className="line-clamp-2 text-sm text-body [overflow-wrap:anywhere]">{a.fields?.find((f) => f.label === 'Subject')?.value ?? a.summary}</span>}
                <span className="text-xs text-muted">{formatWhen(a.decided_at ?? a.at)}</span>
              </button>
            </li>
          )
        })}
        {data && rows.length === 0 && (
          <li className="rounded-xl border border-dashed border-line p-5 text-center text-sm text-muted">
            {tab === 'waiting' ? 'Nothing is waiting for you.' : 'Nothing decided yet.'}
          </li>
        )}
      </ul>
      {tab === 'waiting' && expired.length > 0 && (
        <>
          <h3 className="mt-5 mb-2 text-sm font-medium text-ink">Ran out of time. Ask {name} again if you still want these.</h3>
          <ul className="space-y-1.5" aria-label="Requests that ran out of time">
            {expired.map((a) => (
              <li key={a.id} className="rounded-xl border border-dashed border-line bg-white/60 p-3">
                <p className="text-sm font-medium text-ink">
                  {KIND_LABEL[a.kind] ?? a.kind} <Badge variant="secondary">Expired</Badge>
                </p>
                <p className="mt-0.5 line-clamp-2 break-words text-sm text-body">{a.summary}</p>
                <p className="mt-1 text-xs text-muted">Asked {formatWhen(a.at)} · not done, it was not answered in time</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )

  const detail = current && (
    <article ref={current.status === 'pending' ? seenBeacon(current.id) : undefined} aria-label={rowTitle(current)} className="flex min-h-full max-w-3xl flex-col pt-4">
      <Button variant="ghost" size="sm" className="mb-2 -ml-2 self-start @3xl/md:hidden" onClick={() => choose(null)}>
        <ArrowLeft aria-hidden /> All requests
      </Button>
      <div className="flex-1 rounded-xl border border-line bg-white p-4 @3xl/md:p-6">
        <ApprovalBody
          a={current}
          name={name}
          ticks={d.ticks}
          onSign={(v) => void d.setSign(current.id, v)}
          reasons={d.reasons}
          onReason={(v) => d.setReasons({ ...d.reasons, [current.id]: v })}
          readOnly={current.status !== 'pending'}
        />
        {current.status !== 'pending' && (
          <div className="mt-3 border-t border-line pt-3 text-sm" data-testid="approval-outcome">
            <p className="font-medium text-ink">
              {STATUS_LABEL[current.status]} {current.decided_at ? formatWhen(current.decided_at) : ''}
            </p>
            {current.result && <p className="mt-0.5 break-words text-body">{current.result}</p>}
          </div>
        )}
      </div>
      {current.status === 'pending' && (
        <div className="sticky bottom-0 -mx-4 mt-3 flex items-center justify-end gap-2 border-t border-line/70 bg-canvas/95 px-4 py-3 backdrop-blur @3xl/md:-mx-8 @3xl/md:px-8">
          <ApprovalActions a={current} busy={d.busy === current.id} onDecide={(v) => void d.decide(current.id, v)} />
        </div>
      )}
    </article>
  )

  return (
    <MasterDetail
      header={header}
      list={list}
      listLabel="Requests"
      detail={detail}
      detailLabel="Request"
      selected={Boolean(current)}
      empty={<p className="pt-10 text-center text-sm text-muted">Choose a request to read it in full.</p>}
      listWidth="lg"
    />
  )
}

