import { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GET, POST } from './route'

// The /api proxy is the only way from the internet to the backend (#80). The e2e `proxy` project runs the same checks against a
// built server; these run in milliseconds and pin the rules down: which prefixes pass, what never reaches the backend, and which
// client-address headers are forwarded.
let seen: { url: string; headers: Headers }[]

beforeEach(() => {
  seen = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, headers: new Headers(init.headers) })
      return new Response('{}', { status: 200 })
    }),
  )
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

const call = (path: string, headers: Record<string, string> = {}, method: 'GET' | 'POST' = 'GET') => {
  const req = new NextRequest(`http://app.test/api/${path}`, { method, headers })
  const segments = path.split('?')[0].split('/')
  return (method === 'GET' ? GET : POST)(req, { params: Promise.resolve({ path: segments }) })
}

describe('which paths get through', () => {
  it.each(['auth/login', 'app/agents', 'health', 'p/ask/abc', 'api/overview'])('forwards /%s', async (path) => {
    expect((await call(path)).status).toBe(200)
    expect(seen).toHaveLength(1)
  })

  // `api` stays on the list: the operator console calls /api/api/* (the backend's own /api prefix).
  it('keeps the operator console prefix /api/api reachable', async () => {
    await call('api/overview')
    expect(seen[0].url).toMatch(/\/api\/overview$/)
  })

  it.each(['docs', 'openapi.json', 'webhooks/telegram', 'admin', 'metrics'])('refuses /%s (not a public prefix)', async (path) => {
    expect((await call(path)).status).toBe(404)
    expect(seen).toHaveLength(0)
  })

  // Dot and empty segments, as decoded by Next, and segments that smuggle a slash or backslash.
  it.each([
    ['auth', '..', 'docs'],
    ['auth', '.', 'me'],
    ['auth', '', 'me'],
    ['', 'docs'],
    ['auth', '../docs'],
    ['auth', '..\\docs'],
    ['auth', 'me\u0000'],
  ])('never forwards the segments %j', async (...segments) => {
    const req = new NextRequest('http://app.test/api/x')
    const res = await GET(req, { params: Promise.resolve({ path: segments }) })
    expect(res.status).toBe(404)
    expect(seen).toHaveLength(0)
  })

  it('refuses an empty path', async () => {
    const res = await GET(new NextRequest('http://app.test/api'), { params: Promise.resolve({ path: [] }) })
    expect(res.status).toBe(404)
  })

  it('encodes each segment, so a decoded slash cannot become a new path level', async () => {
    await GET(new NextRequest('http://app.test/api/auth/x'), { params: Promise.resolve({ path: ['auth', 'a b'] }) })
    expect(seen[0].url).toMatch(/\/auth\/a%20b$/)
  })
})

describe('client address headers (FE-26)', () => {
  const forged = { 'x-forwarded-for': '6.6.6.6', 'x-real-ip': '6.6.6.6', forwarded: 'for=6.6.6.6' }

  it('drops a client-supplied address by default, so a browser cannot choose its own rate-limit bucket', async () => {
    await call('auth/login', forged, 'POST')
    for (const h of Object.keys(forged)) expect(seen[0].headers.get(h), h).toBeNull()
  })

  it('passes the load balancer address on only when TRUST_PROXY_HEADERS=true', async () => {
    vi.stubEnv('TRUST_PROXY_HEADERS', 'true')
    await call('auth/login', { 'x-forwarded-for': '203.0.113.9' }, 'POST')
    expect(seen[0].headers.get('x-forwarded-for')).toBe('203.0.113.9')
  })

  it.each(['TRUE', '1', 'yes', ''])('treats TRUST_PROXY_HEADERS=%j as off', async (value) => {
    vi.stubEnv('TRUST_PROXY_HEADERS', value)
    await call('auth/login', forged, 'POST')
    expect(seen[0].headers.get('x-forwarded-for')).toBeNull()
  })

  it('always tells the API the host and scheme the browser used', async () => {
    await call('auth/me', { host: 'app.test' })
    expect(seen[0].headers.get('x-forwarded-host')).toBe('app.test')
    expect(seen[0].headers.get('x-forwarded-proto')).toBe('http')
  })
})

it('answers 502 with a plain explanation when the API is down', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('ECONNREFUSED'))))
  const res = await call('health')
  expect(res.status).toBe(502)
  expect((await res.json()).detail).toMatch(/Can't reach the Superaide API/)
})
