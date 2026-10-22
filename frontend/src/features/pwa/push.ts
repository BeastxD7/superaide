// The browser side of web push (ADR 0011): subscribe after the owner taps "Turn on
// notifications", re-save the subscription each time the app opens (iOS never says when one
// changes), and drop it on sign-out. The server answers 404 for all of this while push is off.
import { platformOf } from '@/features/pwa/display'

const BASE = '/api/app/push'
const HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'superaide' }

export function pushSupported(): boolean {
  return typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

async function save(sub: PushSubscription): Promise<boolean> {
  const json = sub.toJSON()
  const res = await fetch(`${BASE}/subscription`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: HEADERS,
    body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, platform: platformOf() }),
  })
  return res.ok
}

// Called from a tap (browsers require a user gesture for the permission prompt).
export async function turnOnNotifications(): Promise<'on' | 'denied' | 'unavailable'> {
  if (!pushSupported()) return 'unavailable'
  const keyRes = await fetch(`${BASE}/key`, { credentials: 'same-origin', headers: HEADERS })
  if (!keyRes.ok) return 'unavailable'
  const { public_key } = (await keyRes.json()) as { public_key: string }
  if ((await Notification.requestPermission()) !== 'granted') return 'denied'
  const reg = await navigator.serviceWorker.ready
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) }))
  return (await save(sub)) ? 'on' : 'unavailable'
}

// Each start of the app: refresh the server's copy (it may have changed or been lost).
export async function resaveSubscription(): Promise<'saved' | 'none' | 'skipped'> {
  if (!pushSupported() || Notification.permission !== 'granted') return 'skipped'
  const reg = await navigator.serviceWorker.getRegistration('/')
  const sub = await reg?.pushManager.getSubscription()
  if (!sub) return 'none'
  return (await save(sub).catch(() => false)) ? 'saved' : 'skipped'
}

// Sign-out: forget this browser on the server and end the subscription in the browser itself,
// so a shared device stops getting pushes even if the server call fails.
export async function dropSubscription(): Promise<void> {
  if (!pushSupported()) return
  const reg = await navigator.serviceWorker.getRegistration('/')
  const sub = await reg?.pushManager.getSubscription()
  if (!sub) return
  await fetch(`${BASE}/subscription`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: HEADERS,
    body: JSON.stringify({ endpoint: sub.endpoint }),
  }).catch(() => undefined)
  await sub.unsubscribe().catch(() => false)
}
