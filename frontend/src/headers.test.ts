import { afterEach, describe, expect, it, vi } from 'vitest'

// The static security headers (#80, FE-02). Pages get their Content-Security-Policy (with a nonce) from src/proxy.ts; these are
// the ones next.config.ts sets on every response, and the fixed policy on API answers.
async function headersFor(env: 'production' | 'development') {
  vi.resetModules()
  vi.stubEnv('NODE_ENV', env)
  const config = (await import('../next.config')).default
  const rules = await config.headers!()
  const pick = (source: string) => Object.fromEntries(rules.find((r) => r.source === source)!.headers.map((h) => [h.key, h.value]))
  return { all: pick('/:path*'), api: pick('/api/:path*') }
}
afterEach(() => vi.unstubAllEnvs())

describe('security headers', () => {
  it('sends HSTS for a year in production, and not on the plain-http development server', async () => {
    expect((await headersFor('production')).all['Strict-Transport-Security']).toBe('max-age=31536000')
    expect((await headersFor('development')).all['Strict-Transport-Security']).toBeUndefined()
  })

  it('always sends nosniff, a referrer policy, no framing and a camera/microphone policy limited to this site', async () => {
    const { all } = await headersFor('production')
    expect(all['X-Content-Type-Options']).toBe('nosniff')
    expect(all['Referrer-Policy']).toBe('strict-origin-when-cross-origin')
    expect(all['X-Frame-Options']).toBe('DENY')
    expect(all['Permissions-Policy']).toBe('camera=(self), microphone=(self), geolocation=()')
  })

  it('gives API answers a locked-down policy: nothing loads, nothing frames them, images only from here', async () => {
    const csp = (await headersFor('production')).api['Content-Security-Policy']
    expect(csp).toContain("default-src 'none'")
    for (const rule of ["img-src 'self' data:", "connect-src 'self'", "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'"]) {
      expect(csp).toContain(rule)
    }
    expect(csp).not.toContain('unsafe')
  })
})

describe('the page policy from proxy.ts', () => {
  it('has a nonce, no inline scripts and no remote anything', async () => {
    vi.resetModules()
    vi.stubEnv('NODE_ENV', 'production')
    const { contentSecurityPolicy } = await import('./proxy')
    const csp = contentSecurityPolicy('abc123')
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'")
    expect(csp).not.toMatch(/script-src[^;]*unsafe-(inline|eval)/)
    expect(csp).toMatch(/style-src 'self' 'nonce-abc123'(;|$)/)
    for (const rule of ["img-src 'self' data:", "connect-src 'self'", "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'", "form-action 'self'"]) {
      expect(csp).toContain(rule)
    }
    expect(csp).not.toMatch(/https?:|\*/) // no remote host and no wildcard
  })
})
