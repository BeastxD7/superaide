import type { SourceRef } from '@/lib/api'

// Citation marks. Ava's saved text carries plain "[1]" marks that match the numbered Sources
// list. This turns them into links with the made-up scheme "cite:" (which Markdown.tsx renders
// as a button) and takes out the repeats: one mark per source per paragraph. A mark with no
// matching source stays plain text. Code is left alone.

export const CITE_SCHEME = 'cite:'

const TOKEN = /(```[\s\S]*?```|`[^`\n]*`)|(\n[ \t]*\n)|([ \t]*\[(\d{1,3})\](?![(:]))/g

export function markCitations(text: string, sources: SourceRef[] | undefined): string {
  if (!sources || sources.length === 0 || !text.includes('[')) return text
  const known = new Set(sources.map((s) => s.n))
  let seen = new Set<number>()
  return text.replace(TOKEN, (all, code, blank, _mark, num) => {
    if (code) return all
    if (blank) {
      seen = new Set()
      return all
    }
    const n = Number(num)
    if (!known.has(n)) return all
    if (seen.has(n)) return ''
    seen.add(n)
    return `[${n}](${CITE_SCHEME}${n})`
  })
}

export function citationNumber(href: string | undefined): number | null {
  if (!href || !href.startsWith(CITE_SCHEME)) return null
  const n = Number(href.slice(CITE_SCHEME.length))
  return Number.isInteger(n) && n > 0 ? n : null
}

const KIND_LABEL: Record<SourceRef['kind'], string> = {
  memory: 'Note',
  message: 'Message',
  web: 'Web',
  calendar: 'Calendar',
  email: 'Email',
  approval: 'Approval',
  other: 'Source',
}

export const sourceKindLabel = (kind: SourceRef['kind']): string => KIND_LABEL[kind]

export function isWebUrl(uri: string): boolean {
  try {
    const u = new URL(uri)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}

// The backend titles a note "Note · preference · from the app": it already starts with the kind.
export function sourceHeading(src: SourceRef): { kind: string; title: string } {
  const kind = KIND_LABEL[src.kind]
  const title = src.title || (src.kind === 'web' ? src.uri : '')
  return title.startsWith(`${kind} ·`) ? { kind: '', title } : { kind, title }
}

export function sourceLabel(src: SourceRef): string {
  const { kind, title } = sourceHeading(src)
  return `Source ${src.n}: ${[kind, title].filter(Boolean).join(', ').replaceAll(' · ', ', ')}`
}

export type SourceLink = { href: string; text: string; external: boolean }

const MEMORY = /^ava:\/\/memory\/([0-9a-f-]{8,64})$/i
const MAIL = /^ava:\/\/mail\/([0-9a-z]{6,64})$/i

// Where a source leads, built from the stored id by code (never from model text). Notes open
// in the Notebook, emails in Gmail and web pages as themselves. A chat message, a calendar item
// or a shared-file passage has no page of its own yet, so it has no link.
export function sourceLink(src: SourceRef): SourceLink | null {
  if (src.kind === 'web') return isWebUrl(src.uri) ? { href: src.uri, text: 'Open the page', external: true } : null
  const memory = MEMORY.exec(src.uri)
  if (src.kind === 'memory' && memory) return { href: `/dashboard/notebook#note-${memory[1]}`, text: 'Open in the Notebook', external: false }
  const mail = MAIL.exec(src.uri)
  if (src.kind === 'email' && mail) return { href: `https://mail.google.com/mail/u/0/#all/${mail[1]}`, text: 'Open in Gmail', external: true }
  return null
}

// "Telegram" for ava://msg/telegram/<id>, so a message source says where it was said.
export function messageChannel(uri: string): string | null {
  const m = /^ava:\/\/msg\/([a-z]+)\//.exec(uri)
  return m ? m[1].charAt(0).toUpperCase() + m[1].slice(1) : null
}
