import { useMemo, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CitationMark } from '@/components/Citations'
import type { SourceRef } from '@/lib/api'
import { CITE_SCHEME, citationNumber, markCitations } from '@/lib/citations'

// Ava writes light Markdown (bold, lists, links). Raw HTML in a message is never
// rendered (react-markdown escapes it), and links open in a new tab. Pictures are never
// loaded: the text may come from a web page or a stranger's message, and an image address can
// carry the owner's private details out as soon as the browser fetches it. The picture's
// description is shown as plain text instead.
function ExternalLink({ href, children }: { href?: string; children?: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium text-violet-700 underline">
      {children}
    </a>
  )
}

const components: Components = {
  p: ({ children }) => <p className="leading-relaxed [&:not(:first-child)]:mt-2">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  ul: ({ children }) => <ul className="mt-2 list-disc space-y-1 pl-5 first:mt-0">{children}</ul>,
  ol: ({ children }) => <ol className="mt-2 list-decimal space-y-1 pl-5 first:mt-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => <ExternalLink href={href}>{children}</ExternalLink>,
  img: ({ alt }) => (alt ? <span className="italic text-muted">{alt}</span> : null),
  code: ({ children }) => <code className="rounded bg-mist px-1 py-0.5 text-[0.9em]">{children}</code>,
  h1: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
}

// "cite:" links are the [n] marks that markCitations() made; every other address goes through the default check.
const urlTransform = (url: string) => (url.startsWith(CITE_SCHEME) ? url : defaultUrlTransform(url))

export function Markdown({ text, sources }: { text: string; sources?: SourceRef[] }) {
  const marked = useMemo(() => markCitations(text, sources), [text, sources])
  const withMarks = useMemo<Components>(() => {
    if (!sources || sources.length === 0) return components
    return {
      ...components,
      a: ({ href, children }) => {
        const n = citationNumber(href)
        const src = n === null ? undefined : sources.find((s) => s.n === n)
        if (n !== null) return src ? <CitationMark src={src} /> : <>{children}</>
        return <ExternalLink href={href}>{children}</ExternalLink>
      },
    }
  }, [sources])
  return (
    <div className="[overflow-wrap:anywhere]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={withMarks} urlTransform={urlTransform}>
        {marked}
      </ReactMarkdown>
    </div>
  )
}
