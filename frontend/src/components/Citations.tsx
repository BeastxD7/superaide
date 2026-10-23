'use client'

import { X } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { SourceRef } from '@/lib/api'
import { messageChannel, sourceHeading, sourceLabel, sourceLink } from '@/lib/citations'

// What one source is: its kind and title, the exact words used, and where to go. Shared by the
// popover on a [n] mark and by the Sources list under the message.
export function SourceBody({ src, titleId }: { src: SourceRef; titleId?: string }) {
  const link = sourceLink(src)
  const channel = src.kind === 'message' ? messageChannel(src.uri) : null
  const { kind, title } = sourceHeading(src)
  return (
    <span className="block min-w-0 [overflow-wrap:anywhere]">
      <span id={titleId} className="block text-xs">
        {kind && <span className="font-medium text-ink">{kind}</span>}
        {title && <span className={kind ? 'text-body' : 'font-medium text-ink'}>{kind ? ` · ${title}` : title}</span>}
        {channel && <span className="text-body">{` · from ${channel}`}</span>}
      </span>
      {src.cited_text && <span className="mt-1 block text-xs text-muted">&ldquo;{src.cited_text}&rdquo;</span>}
      {link &&
        (link.external ? (
          <a href={link.href} target="_blank" rel="noopener noreferrer" className="mt-1.5 inline-block text-xs font-medium text-violet-700 underline">
            {link.text}
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : (
          <Link href={link.href} className="mt-1.5 inline-block text-xs font-medium text-violet-700 underline">
            {link.text}
          </Link>
        ))}
    </span>
  )
}

const SHEET_QUERY = '(max-width: 639px)'

// A small superscript button for a [n] mark. Click, Enter or Space (or hovering with a mouse)
// opens a popover with the source; Escape or a click elsewhere closes it. On a phone it is a
// bottom sheet. The popover is portalled to the body so no transformed ancestor can displace it.
export function CitationMark({ src }: { src: SourceRef }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const hoverTimer = useRef<number | undefined>(undefined)
  const viaHover = useRef(false)
  const id = useId()
  const label = sourceLabel(src)

  const place = useCallback(() => {
    const b = btn.current?.getBoundingClientRect()
    if (!b) return
    const width = Math.min(320, window.innerWidth - 16)
    const left = Math.max(8, Math.min(b.left - 8, window.innerWidth - width - 8))
    setPos({ left, top: b.bottom + 6 })
  }, [])

  const close = useCallback((returnFocus: boolean) => {
    window.clearTimeout(hoverTimer.current)
    setOpen(false)
    if (returnFocus) btn.current?.focus()
  }, [])

  function show(hover: boolean) {
    window.clearTimeout(hoverTimer.current)
    viaHover.current = hover
    place()
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    if (!viaHover.current) pop.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close(true)
      }
    }
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!pop.current?.contains(t) && !btn.current?.contains(t)) close(false)
    }
    const onMove = () => place() // the page may still be scrolling to the newest message: follow the mark
    document.addEventListener('keydown', onKey, true)
    document.addEventListener('pointerdown', onDown, true)
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onMove, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onMove, true)
    }
  }, [open, close, place])

  const sheet = typeof window !== 'undefined' && window.matchMedia(SHEET_QUERY).matches
  const leave = () => {
    if (!viaHover.current) return
    window.clearTimeout(hoverTimer.current)
    hoverTimer.current = window.setTimeout(() => close(false), 180)
  }
  const hoverable = typeof window !== 'undefined' && window.matchMedia('(hover: hover)').matches

  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        data-testid="citation-mark"
        onClick={() => (open && !viaHover.current ? close(false) : show(false))}
        onMouseEnter={() => hoverable && !open && show(true)}
        onMouseLeave={leave}
        className="relative mx-0.5 inline-flex size-4 -translate-y-1 items-center justify-center rounded-full bg-violet-100 align-baseline text-[10px] leading-none font-semibold text-violet-800 hover:bg-violet-200 focus-visible:ring-2 focus-visible:ring-violet-600 focus-visible:outline-none before:absolute before:-inset-2 before:content-['']"
      >
        {src.n}
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={pop}
            id={id}
            role="dialog"
            aria-label={label}
            tabIndex={-1}
            data-testid="citation-popover"
            onMouseEnter={() => window.clearTimeout(hoverTimer.current)}
            onMouseLeave={leave}
            style={sheet ? undefined : { left: pos.left, top: pos.top, width: Math.min(320, window.innerWidth - 16) }}
            className={
              sheet
                ? 'fixed inset-x-0 bottom-0 z-50 max-h-[60vh] overflow-y-auto rounded-t-2xl border border-line bg-white p-4 pb-6 shadow-2xl outline-none'
                : 'fixed z-50 max-h-[50vh] overflow-y-auto rounded-xl border border-line bg-white p-3 shadow-xl outline-none'
            }
          >
            <button
              type="button"
              onClick={() => close(true)}
              aria-label="Close source"
              className="float-right -mt-1 -mr-1 ml-2 inline-flex size-8 items-center justify-center rounded-full text-muted hover:bg-mist"
            >
              <X className="size-4" />
            </button>
            <SourceBody src={src} />
          </div>,
          document.body,
        )}
    </>
  )
}
