// Ava's service worker (ADR 0011 M0). Registered only when NEXT_PUBLIC_PWA=1.
//
// It handles push notifications and taps on them, and nothing else. There is deliberately no
// fetch handler, so it caches nothing: no API responses, no pages, no personal data. Each push
// is written by the server to be safe on a lock screen ("Ava / Needs you"); this script shows
// what the server sent and never adds anything to it.
'use strict'

const FALLBACK = { title: 'Ava', body: 'Needs you' }

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

function sameOrigin(url) {
  try {
    const u = new URL(url, self.location.origin)
    return u.origin === self.location.origin ? u.href : null
  } catch {
    return null
  }
}

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  const n = (data && data.notification) || {}
  const meta = (data && data.superaide) || {}
  const title = typeof n.title === 'string' && n.title ? n.title : FALLBACK.title
  const body = typeof n.body === 'string' && n.body ? n.body : FALLBACK.body
  const options = {
    body,
    tag: typeof n.tag === 'string' ? n.tag : undefined,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: sameOrigin(n.navigate) || '/dashboard', token: meta.token || null, kind: meta.kind || null },
  }
  const shown = self.registration.showNotification(title, options)
  // Every push must show a notification, or Safari ends the subscription after three.
  const badge =
    typeof n.app_badge === 'number' && self.navigator.setAppBadge
      ? self.navigator.setAppBadge(n.app_badge).catch(() => undefined)
      : Promise.resolve()
  event.waitUntil(Promise.all([shown, badge]))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  // Acknowledge and Snooze buttons come with the escalation ladder (M4). Until then, a tap opens
  // the item; opening it never counts as an acknowledgement.
  const url = (event.notification.data && event.notification.data.url) || '/dashboard'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if ('focus' in w && new URL(w.url).origin === self.location.origin) {
          return w.focus().then((c) => (c && 'navigate' in c ? c.navigate(url) : undefined))
        }
      }
      return self.clients.openWindow(url)
    }),
  )
})

self.addEventListener('notificationclose', () => {
  // Nothing to record: dismissing a notification is not an acknowledgement.
})
