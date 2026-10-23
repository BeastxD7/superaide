// Runs before every page and API request.
//  1. A per-request Content-Security-Policy with a nonce: only scripts (and style elements) that carry this request's random
//     nonce run, so an injected <script> or inline handler does nothing. Next reads the nonce from the header and stamps it on its
//     own scripts and styles. Pages therefore render per request (the root layout reads the session cookie, so they already do).
//  2. Signed-out visitors who open a page that needs an account go to log in (or sign up, when hiring) and come back afterwards.
//     It only checks that a session cookie exists; the API decides whether it's valid.
import { NextResponse, type NextRequest } from 'next/server'

const SESSION_COOKIE = process.env.SESSION_COOKIE_NAME ?? 'superaide_session'
const dev = process.env.NODE_ENV !== 'production'
const GUARDED = /^\/(meet|workspace|dashboard(\/.*)?)$/

// What the browser may load (#69, #80). Pictures only from this site or inline, so text from a web page or a stranger can't send
// private details out through an image address; the app talks only to itself (the /api proxy).
//   script-src  nonce + 'strict-dynamic', no 'unsafe-inline'. The dev server also needs eval for its debugging.
//   style-src   nonce, no 'unsafe-inline' (dev keeps it: hot reload injects unmarked <style>).
//   style-src-attr 'unsafe-inline' is the one remaining exception: React and Motion write style="" attributes (positions, animation
//     values), which a nonce cannot cover. An attribute can only style, never run script.
export function contentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ''}`,
    dev ? "style-src 'self' 'unsafe-inline'" : `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src 'self'${dev ? ' ws: wss:' : ''}`,
    "media-src 'self' blob:",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ')
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  const nonce = btoa(crypto.randomUUID())
  const csp = contentSecurityPolicy(nonce)

  if (GUARDED.test(pathname) || /^\/agents\/[^/]+\/hire$/.test(pathname)) {
    if (!request.cookies.has(SESSION_COOKIE)) {
      const hiring = /^\/agents\/[^/]+\/hire$/.test(pathname)
      const url = request.nextUrl.clone()
      url.pathname = hiring ? '/signup' : '/login'
      url.search = `?next=${encodeURIComponent(pathname)}`
      const redirect = NextResponse.redirect(url)
      redirect.headers.set('Content-Security-Policy', csp)
      return redirect
    }
  }

  const requestHeaders = new Headers(request.headers)
  requestHeaders.set('x-nonce', nonce)
  requestHeaders.set('Content-Security-Policy', csp)
  const response = NextResponse.next({ request: { headers: requestHeaders } })
  response.headers.set('Content-Security-Policy', csp)
  return response
}

export const config = {
  // Pages only. Not /api: the proxy would buffer upload bodies (Next caps that at 10 MB, which truncated the files the introduction
  // refuses as too big), and API answers need no nonce; next.config.ts gives them a fixed policy. Page prefetches carry no nonce of their own.
  matcher: [
    {
      source: '/((?!api/|_next/static|_next/image|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
}
