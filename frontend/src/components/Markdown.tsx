import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Ava writes light Markdown (bold, lists, links). Raw HTML in a message is never
// rendered (react-markdown escapes it), and links open in a new tab. Pictures are never
// loaded: the text may come from a web page or a stranger's message, and an image address can
// carry the owner's private details out as soon as the browser fetches it. The picture's
// description is shown as plain text instead.
const components: Components = {
  p: ({ children }) => <p className="leading-relaxed [&:not(:first-child)]:mt-2">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  ul: ({ children }) => <ul className="mt-2 list-disc space-y-1 pl-5 first:mt-0">{children}</ul>,
  ol: ({ children }) => <ol className="mt-2 list-decimal space-y-1 pl-5 first:mt-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium text-violet-700 underline">
      {children}
    </a>
  ),
  img: ({ alt }) => (alt ? <span className="italic text-muted">{alt}</span> : null),
  code: ({ children }) => <code className="rounded bg-mist px-1 py-0.5 text-[0.9em]">{children}</code>,
  h1: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mt-2 font-semibold first:mt-0">{children}</p>,
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="[overflow-wrap:anywhere]">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
