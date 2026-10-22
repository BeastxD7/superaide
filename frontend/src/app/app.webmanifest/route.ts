import { PWA_ENABLED } from '@/lib/flags'

// The web app manifest (ADR 0011 M0). Served only when NEXT_PUBLIC_PWA=1; otherwise it doesn't exist.
// A route handler (not app/manifest.ts) so the <link rel="manifest"> can be left out while it's off.
export const dynamic = 'force-static'

const START_URL = process.env.NEXT_PUBLIC_PWA_START_URL || '/dashboard/chat'

export function GET() {
  if (!PWA_ENABLED) return new Response('Not found', { status: 404 })
  const manifest = {
    id: '/dashboard', // stable, so iOS Focus settings follow the app
    name: 'Ava',
    short_name: 'Ava',
    description: 'Ava, your personal assistant from Superaide',
    start_url: START_URL,
    scope: '/',
    display: 'standalone',
    orientation: 'portrait',
    theme_color: '#ffffff',
    background_color: '#ffffff',
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icons/maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
      { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  }
  return new Response(JSON.stringify(manifest), {
    headers: { 'Content-Type': 'application/manifest+json', 'Cache-Control': 'public, max-age=3600' },
  })
}
