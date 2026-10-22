'use client'

import { useEffect } from 'react'
import { resaveSubscription } from '@/features/pwa/push'
import { PWA_ENABLED } from '@/lib/flags'

// Registers the service worker (public/sw.js) and re-saves this device's push subscription on
// every start. Renders nothing. Does nothing at all unless NEXT_PUBLIC_PWA=1.
export function PwaSetup({ signedIn }: { signedIn: boolean }) {
  useEffect(() => {
    if (!PWA_ENABLED || !('serviceWorker' in navigator)) return
    navigator.serviceWorker
      .register('/sw.js', { scope: '/', updateViaCache: 'none' })
      .then(() => (signedIn ? resaveSubscription() : undefined))
      .catch(() => undefined)
  }, [signedIn])
  return null
}
