// Same-origin proxy to the backend API: the browser calls /api/<path> on this
// server and we forward it to the FastAPI service. The session cookie therefore
// belongs to the app's own origin, whatever host name the page was opened with.
import type { NextRequest } from 'next/server'
import { API_INTERNAL_URL } from '@/lib/server'

export const dynamic = 'force-dynamic'

// Only the API's public prefixes are reachable through the app (not /docs, /webhooks…).
// `p`: public pages with no account, such as an owner's "Ask my Ava" link.
const ALLOWED = new Set(['auth', 'app', 'api', 'health', 'p'])
// Headers that describe one hop, or that fetch() recomputes, are not copied.
const SKIP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'host',
  'content-length',
  'content-encoding',
])

// `.`, `..` and empty segments are never part of a real API path. Left in, fetch() would tidy them
// away and the request could land on a different route than the one checked above (for example
// /auth/%2e%2e/docs becoming /docs). `api` stays: the operator console calls /api/api/*.
function isPlainPath(path: string[]): boolean {
  // A segment that decoded to something holding a slash or backslash (`..%2Fdocs`) is a path in disguise:
  // it would reach the API as one odd segment today, but nothing downstream should have to be right about that.
  return path.every((segment) => segment !== '' && segment !== '.' && segment !== '..' && !/[/\\\u0000]/.test(segment))
}

type Context = { params: Promise<{ path: string[] }> }

async function forward(req: NextRequest, { params }: Context): Promise<Response> {
  const { path } = await params
  if (!path.length || !isPlainPath(path) || !ALLOWED.has(path[0])) {
    return Response.json({ detail: 'Not Found' }, { status: 404 })
  }
  const target = `${API_INTERNAL_URL}/${path.map(encodeURIComponent).join('/')}${req.nextUrl.search}`

  const headers = new Headers()
  req.headers.forEach((value, key) => {
    if (!SKIP.has(key)) headers.set(key, value)
  })
  // The API rate-limits sign-ups and log-ins per client address. A browser can
  // send any X-Forwarded-For it likes, so drop it unless a trusted load balancer
  // in front of this server overwrites it (TRUST_PROXY_HEADERS=true).
  if (process.env.TRUST_PROXY_HEADERS !== 'true') {
    for (const h of ['x-forwarded-for', 'x-real-ip', 'forwarded']) headers.delete(h)
  }
  headers.set('x-forwarded-host', req.headers.get('host') ?? '')
  headers.set('x-forwarded-proto', req.nextUrl.protocol.replace(':', ''))

  const hasBody = req.method !== 'GET' && req.method !== 'HEAD'
  let upstream: Response
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: 'manual',
      cache: 'no-store',
      // Stream request bodies (file uploads) instead of buffering them.
      ...(hasBody ? { duplex: 'half' } : {}),
    } as RequestInit)
  } catch {
    return Response.json(
      { detail: "Can't reach the Superaide API. Is it running (start.command or `make api`)?" },
      { status: 502 },
    )
  }

  const out = new Headers()
  upstream.headers.forEach((value, key) => {
    if (!SKIP.has(key) && key !== 'set-cookie') out.set(key, value)
  })
  for (const cookie of upstream.headers.getSetCookie()) out.append('set-cookie', cookie)
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out })
}

export { forward as DELETE, forward as GET, forward as HEAD, forward as PATCH, forward as POST, forward as PUT }
