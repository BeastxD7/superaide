// Runs once when the Next.js server starts.
//
// The /api proxy drops X-Forwarded-For unless TRUST_PROXY_HEADERS=true (a browser could otherwise forge it). Without it the
// API sees every visitor as the proxy's own address, so its per-IP limits on sign-up, log-in and the Ask form share one bucket
// for the whole internet. On a public deployment, put a load balancer that overwrites X-Forwarded-For in front and set it.
export function proxyWarning(env: Record<string, string | undefined>): string | null {
  if (env.NODE_ENV !== 'production' || env.TRUST_PROXY_HEADERS === 'true') return null
  const origin = env.PUBLIC_BASE_URL || env.FRONTEND_ORIGIN || ''
  if (!/^https:\/\//i.test(origin) || /^https:\/\/(localhost|127\.0\.0\.1)([:/]|$)/i.test(origin)) return null
  return (
    `[superaide] ${origin} looks public but TRUST_PROXY_HEADERS is not "true": every visitor shares one rate-limit bucket ` +
    '(sign-up, log-in, Ask form). Put a load balancer that sets X-Forwarded-For in front and set TRUST_PROXY_HEADERS=true.'
  )
}

export function register() {
  const warning = proxyWarning(process.env)
  if (warning) console.warn(warning)
}
