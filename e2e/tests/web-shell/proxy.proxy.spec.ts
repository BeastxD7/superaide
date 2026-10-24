// The /api proxy (frontend/src/app/api/[...path]/route.ts) is the only thing between the public internet and the
// backend's admin, docs and webhook routes. These tests run it against scripts/fake-upstream.mjs so they can see
// what it sends upstream and how it treats the answer. Run with:
//
//   cd frontend && npm run build && cd ../e2e && E2E_PROXY=1 E2E_SLOT=91 npx playwright test --project=proxy
import { request as httpRequest } from 'node:http'
import { expect, test } from '@playwright/test'
import { PROXY_A, PROXY_B, PROXY_C } from '../../src/env'

type Raw = { status: number; headers: Record<string, string | string[] | undefined>; body: string }

// A request with the path exactly as written: fetch() and the browser tidy `..` and `%2e%2e` away before sending.
function raw(port: number, method: string, path: string, headers: Record<string, string> = {}, body?: Buffer): Promise<Raw> {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ host: '127.0.0.1', port, method, path, headers }, (res) => {
      const parts: Buffer[] = []
      res.on('data', (c: Buffer) => parts.push(c))
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body: Buffer.concat(parts).toString('utf8') }))
    })
    req.on('error', reject)
    if (body) req.write(body)
    req.end()
  })
}

const A = `http://127.0.0.1:${PROXY_A}`
const B = `http://127.0.0.1:${PROXY_B}`
const C = `http://127.0.0.1:${PROXY_C}`

test.describe('which paths get through', () => {
  const blocked = [
    '/api/docs',
    '/api/openapi.json',
    '/api/webhooks/telegram',
    '/api/admin',
    '/api/auth/%2e%2e/docs',
    '/api/auth/%2E%2E/docs',
    '/api/auth/../docs',
    '/api/auth/./../docs',
    '/api//docs',
    '/api/auth%2f..%2fdocs',
    '/api/auth/..%2fdocs',
    '/api/auth\\..\\docs',
    '/api/%2e%2e/docs',
    '/api/',
  ]
  for (const path of blocked) {
    test(`${path} never reaches the upstream`, async () => {
      const res = await raw(PROXY_A, 'GET', path)
      // Refused here (404), or tidied by Next into a path that is not an API route: never an upstream answer.
      expect([308, 404, 400]).toContain(res.status)
      expect(res.body).not.toContain('from upstream') // anything the fake upstream answered says so
      expect(res.body).not.toContain('"method"') // or is its echo
    })
  }

  test('the public prefixes are forwarded, including the console prefix /api/api', async () => {
    for (const [path, upstream] of [
      ['/api/auth/x', '/auth/x'],
      ['/api/app/x', '/app/x'],
      ['/api/p/x', '/p/x'],
      ['/api/health', '/health'],
      ['/api/api/x', '/api/x'],
    ]) {
      const res = await raw(PROXY_A, 'GET', path)
      if (path === '/api/health') expect(res.status, path).toBe(200)
      else expect(JSON.parse(res.body), path).toEqual({ detail: 'from upstream', path: upstream })
    }
    const echo = await raw(PROXY_A, 'GET', '/api/api/echo?x=1')
    expect(JSON.parse(echo.body).url).toBe('/api/echo?x=1')
  })
})

test.describe('headers sent upstream', () => {
  const spoof = { 'x-forwarded-for': '203.0.113.9', 'x-real-ip': '203.0.113.9', forwarded: 'for=203.0.113.9', 'x-custom': 'kept' }

  test('a client-supplied x-forwarded-for is dropped by default; host and proto are set; hop-by-hop headers are not copied', async () => {
    const res = await raw(PROXY_A, 'GET', '/api/api/echo', { ...spoof, host: 'app.example.test', 'keep-alive': 'timeout=5' })
    const sent = JSON.parse(res.body).headers as Record<string, string>
    expect(sent['x-forwarded-for']).toBeUndefined()
    expect(sent['x-real-ip']).toBeUndefined()
    expect(sent['forwarded']).toBeUndefined()
    expect(sent['x-forwarded-host']).toBe('app.example.test')
    expect(sent['x-forwarded-proto']).toBe('http')
    expect(sent['x-custom']).toBe('kept')
    expect(sent['keep-alive']).toBeUndefined()
    expect(sent['host']).not.toBe('app.example.test') // the upstream sees its own host, not the client's
  })

  test('with TRUST_PROXY_HEADERS=true the load balancer headers pass through', async () => {
    const res = await raw(PROXY_B, 'GET', '/api/api/echo', spoof)
    const sent = JSON.parse(res.body).headers as Record<string, string>
    expect(sent['x-forwarded-for']).toBe('203.0.113.9')
  })
})

test.describe('what comes back', () => {
  test('every Set-Cookie is kept, not just the first', async () => {
    const res = await raw(PROXY_A, 'GET', '/api/api/cookies')
    expect(res.headers['set-cookie']).toEqual(['a=1; Path=/; HttpOnly', 'b=2; Path=/; SameSite=Lax'])
  })

  test('204 and 304 come back with no body', async () => {
    const no = await raw(PROXY_A, 'GET', '/api/api/status/204')
    expect(no.status).toBe(204)
    expect(no.body).toBe('')
    const notModified = await raw(PROXY_A, 'GET', '/api/api/status/304')
    expect(notModified.status).toBe(304)
    expect(notModified.body).toBe('')
  })

  test('HEAD is answered without a body', async () => {
    const res = await raw(PROXY_A, 'HEAD', '/api/api/echo')
    expect(res.status).toBe(200)
    expect(res.body).toBe('')
  })

  test('a gzip answer reaches the client readable: content-encoding and content-length are not passed on stale', async () => {
    const res = await raw(PROXY_A, 'GET', '/api/api/gzip', { 'accept-encoding': 'identity' })
    expect(res.headers['content-encoding']).toBeUndefined()
    expect(JSON.parse(res.body)).toEqual({ hello: 'world' })
  })

  test('an upstream redirect is passed on, not followed or rewritten', async () => {
    const res = await raw(PROXY_A, 'GET', '/api/api/redirect')
    expect(res.status).toBe(302)
    expect(res.headers['location']).toBe('/api/echo')
  })

  test('an event stream is passed through as it arrives, not buffered to the end', async () => {
    const started = Date.now()
    const firstAt = await new Promise<number>((resolve, reject) => {
      const req = httpRequest({ host: '127.0.0.1', port: PROXY_A, path: '/api/api/sse' }, (res) => {
        res.once('data', () => {
          resolve(Date.now() - started)
          res.destroy()
        })
      })
      req.on('error', (e) => (String(e).includes('aborted') || String(e).includes('ECONNRESET') ? undefined : reject(e)))
      req.end()
    })
    expect(firstAt).toBeLessThan(1200) // the upstream pauses 1.5 s before its second event
  })

  test('a multipart upload of a few megabytes arrives whole, with its content type', async () => {
    const boundary = '----e2eboundary'
    const file = Buffer.alloc(3 * 1024 * 1024, 97)
    const body = Buffer.concat([
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\n`),
      file,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ])
    const res = await raw(PROXY_A, 'POST', '/api/api/upload', { 'content-type': `multipart/form-data; boundary=${boundary}`, 'content-length': String(body.length) }, body)
    expect(res.status).toBe(200)
    const out = JSON.parse(res.body) as { size: number; contentType: string }
    expect(out.size).toBe(body.length)
    expect(out.contentType).toContain('multipart/form-data')
  })

  test('with the upstream down the client gets a 502 and a plain explanation', async () => {
    const res = await raw(PROXY_C, 'GET', '/api/api/echo')
    expect(res.status).toBe(502)
    expect(JSON.parse(res.body).detail).toMatch(/Can't reach the Superaide API/)
  })
})

test('the three servers are the ones under test', async () => {
  for (const base of [A, B, C]) expect((await fetch(`${base}/icon.svg`)).status).toBe(200)
})
