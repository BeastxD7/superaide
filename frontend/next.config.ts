import type { NextConfig } from 'next'

const securityHeaders = [
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
  // Next's dev server blocks its own scripts for other host names; start.command
  // users may open the app as 127.0.0.1 as well as localhost, and DEV_ORIGINS adds
  // more (comma-separated), e.g. the Mac's Wi-Fi address to try it on a phone.
  allowedDevOrigins: ['127.0.0.1', ...(process.env.DEV_ORIGINS ?? '').split(',').map((h) => h.trim()).filter(Boolean)],
  // Keep Next's dev badge clear of the dashboard's account button (bottom left).
  devIndicators: { position: 'bottom-right' },
  reactStrictMode: true,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }]
  },
}

export default nextConfig
