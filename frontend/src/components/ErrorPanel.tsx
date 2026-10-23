'use client'

import { useEffect, useState } from 'react'
import { auth } from '@/lib/api'

const PRIMARY = 'inline-flex min-h-11 items-center rounded-full bg-ink px-5 py-3 text-sm font-medium text-white hover:bg-night-800'
const SECONDARY = 'inline-flex min-h-11 items-center rounded-full border border-line bg-white px-5 py-3 text-sm font-medium text-ink hover:bg-mist'

// What a signed-in or signed-out visitor sees when a screen crashes: plain words, a way to try
// again, and a way out that does not depend on the broken screen (plain links, a fresh page load).
export function ErrorPanel({
  error,
  retry,
  fullPage,
  home,
  signedIn,
}: {
  error: unknown
  retry: () => void
  fullPage?: boolean
  home: { href: string; label: string }
  signedIn?: boolean
}) {
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    // There is no error-reporting service yet; the browser console keeps the details for support.
    console.error(error)
  }, [error])

  async function logOut() {
    setLeaving(true)
    try {
      await auth.logout()
    } catch {
      // If the API is down the cookie stays, but the login page still opens and says what to do.
    }
    window.location.assign('/login')
  }

  return (
    <div className={fullPage ? 'grid min-h-dvh place-items-center bg-canvas px-6' : 'grid flex-1 place-items-center px-6 py-16'}>
      <div className="max-w-md text-center" role="alert">
        <h1 className="text-3xl font-medium text-ink tight">This page hit a problem</h1>
        <p className="mt-3 text-body">
          Nothing you saved has been lost. Try again. If it keeps happening, go back to the start
          {signedIn ? ', or log out and sign in again.' : '.'}
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <button type="button" onClick={() => retry()} className={PRIMARY}>
            Try again
          </button>
          <a href={home.href} className={SECONDARY}>
            {home.label}
          </a>
          {signedIn && (
            <button type="button" onClick={() => void logOut()} disabled={leaving} className={SECONDARY}>
              Log out
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
