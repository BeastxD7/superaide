import type { Account } from '@/lib/api'

// Where a signed-in account belongs: its dashboard, the introduction, or the marketplace.
export function homeFor(account: Account | null): string {
  const hire = account?.hire
  if (!hire) return '/'
  return hire.onboarding_done ? '/dashboard' : '/meet'
}

// Only follow "next" to a page inside this app (never to another site).
export function safeNext(next: string | null): string | null {
  return next && /^\/[a-z0-9/_-]*$/i.test(next) && !next.startsWith('//') ? next : null
}
