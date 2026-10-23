import { describe, expect, it } from 'vitest'
import { proxyWarning } from '@/instrumentation'

const prod = { NODE_ENV: 'production' }

describe('proxyWarning (FE-26)', () => {
  it('warns on a public https deployment that does not trust the proxy headers', () => {
    expect(proxyWarning({ ...prod, FRONTEND_ORIGIN: 'https://app.example.com' })).toContain('TRUST_PROXY_HEADERS')
    expect(proxyWarning({ ...prod, PUBLIC_BASE_URL: 'https://app.example.com' })).toContain('app.example.com')
  })
  it('is quiet when the headers are trusted, in development, locally, or without an origin', () => {
    expect(proxyWarning({ ...prod, FRONTEND_ORIGIN: 'https://app.example.com', TRUST_PROXY_HEADERS: 'true' })).toBeNull()
    expect(proxyWarning({ NODE_ENV: 'development', FRONTEND_ORIGIN: 'https://app.example.com' })).toBeNull()
    expect(proxyWarning({ ...prod, FRONTEND_ORIGIN: 'http://localhost:3000' })).toBeNull()
    expect(proxyWarning({ ...prod, FRONTEND_ORIGIN: 'https://localhost:3000' })).toBeNull()
    expect(proxyWarning(prod)).toBeNull()
  })
})
