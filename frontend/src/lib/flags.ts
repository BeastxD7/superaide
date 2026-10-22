// Build-time feature flags (Next.js inlines NEXT_PUBLIC_* when it builds). Both default to off,
// so a normal build behaves exactly as before.

// ADR 0011 M0: the installable app: web app manifest, service worker, install hint.
export const PWA_ENABLED = process.env.NEXT_PUBLIC_PWA === '1'

// ADR 0011 M6: a bottom tab bar on phones (below the md breakpoint). Desktop is unchanged.
export const MOBILE_TABS_ENABLED = process.env.NEXT_PUBLIC_MOBILE_TABS === '1'
