import { useEffect, useState } from 'react'

// Hash routes, so the agent can serve this page from any path without
// server-side routing: #/, #/agents/ava, #/hire/ava, #/meet, #/workspace.
export type Route = { path: string[]; query: URLSearchParams }

function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/'
  const [pathPart, queryPart = ''] = raw.split('?')
  return { path: pathPart.split('/').filter(Boolean), query: new URLSearchParams(queryPart) }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const onChange = () => {
      setRoute(parse(window.location.hash))
      window.scrollTo({ top: 0 })
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function go(path: string, replace = false): void {
  const target = `#${path}`
  if (replace) {
    history.replaceState(null, '', target)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = path
  }
}
