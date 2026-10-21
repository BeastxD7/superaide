// Runs before a page renders: signed-out visitors who open a page that needs an
// account go to log in (or sign up, when hiring) and come back afterwards.
// It only checks that a session cookie exists; the API decides whether it's valid.
import { NextResponse, type NextRequest } from 'next/server'

const SESSION_COOKIE = process.env.SESSION_COOKIE_NAME ?? 'superaide_session'

export function proxy(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next()
  const { pathname } = request.nextUrl
  const hiring = /^\/agents\/[^/]+\/hire$/.test(pathname)
  const url = request.nextUrl.clone()
  url.pathname = hiring ? '/signup' : '/login'
  url.search = `?next=${encodeURIComponent(pathname)}`
  return NextResponse.redirect(url)
}

export const config = {
  matcher: ['/meet', '/workspace', '/dashboard/:path*', '/agents/:slug/hire'],
}
