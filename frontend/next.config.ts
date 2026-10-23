import type { NextConfig } from 'next'

const dev = process.env.NODE_ENV !== 'production'

// Pages get a Content-Security-Policy per request, with a nonce, from src/proxy.ts. API answers (JSON, never rendered as pages)
// get this fixed one from here, so large uploads still stream straight through the /api proxy route.
const apiContentSecurityPolicy = [
  "default-src 'none'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ')

const securityHeaders = [
  // Browsers ignore this over plain http (start.command on localhost); it matters behind https.
  ...(dev ? [] : [{ key: 'Strict-Transport-Security', value: 'max-age=31536000' }]),
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  // The introduction's video call uses the camera and microphone on this site only.
  { key: 'Permissions-Policy', value: 'camera=(self), microphone=(self), geolocation=()' },
]

const nextConfig: NextConfig = {
  // The Docker image builds a self-contained server; `next start` and `next dev` don't need it.
  output: process.env.NEXT_OUTPUT === 'standalone' ? 'standalone' : undefined,
  poweredByHeader: false,
  // The dev-mode e2e project runs `next dev` next to `next start`; they must not share one .next folder.
  distDir: process.env.NEXT_DIST_DIR || undefined,
  // Next's dev server blocks its own scripts for other host names; start.command
  // users may open the app as 127.0.0.1 as well as localhost, and DEV_ORIGINS adds
  // more (comma-separated), e.g. the Mac's Wi-Fi address to try it on a phone.
  allowedDevOrigins: ['127.0.0.1', ...(process.env.DEV_ORIGINS ?? '').split(',').map((h) => h.trim()).filter(Boolean)],
  // Keep Next's dev badge clear of the dashboard's account button (bottom left).
  devIndicators: { position: 'bottom-right' },
  reactStrictMode: true,
  async headers() {
    return [
      { source: '/:path*', headers: securityHeaders },
      { source: '/api/:path*', headers: [{ key: 'Content-Security-Policy', value: apiContentSecurityPolicy }] },
      // The service worker (ADR 0011): always fetched fresh, so a fix reaches every installed app.
      {
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
          { key: 'Content-Type', value: 'application/javascript; charset=utf-8' },
          { key: 'Service-Worker-Allowed', value: '/' },
        ],
      },
    ]
  },
}

export default nextConfig
